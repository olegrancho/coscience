"""O6: remote jobs are identified, watched, stopped and collected over key-only SSH."""
import time

import pytest

from coscience import remote_exec
from coscience.remote_exec import RemoteToken, check_remote_path, collect, job_state, make_token, parse_token


class ScriptRunner:
    """Answers calls in order; records every argv."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[str]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        return self.replies.pop(0) if self.replies else (0, "", "")


def test_parse_token_tells_remote_from_local():
    assert parse_token("gpu1:4242:777:boot-1") == RemoteToken("gpu1", 4242, "777", "boot-1")
    assert str(RemoteToken("gpu1", 4242, "777", "boot-1")) == "gpu1:4242:777:boot-1"
    assert parse_token("4242:777") is None
    assert parse_token("gpu1:abc:777:boot") is None
    assert parse_token("") is None


def test_a_declared_job_is_identified_on_its_host():
    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    token, state = make_token("gpu1", "gpu1", 4242, runner=runner)
    assert (token, state) == ("gpu1:4242:777:boot-1", "alive")
    assert runner.calls[0][-1].startswith("bash -c ")
    assert "/proc/4242/stat" in runner.calls[0][-1]


@pytest.mark.parametrize("reply, state", [
    ((0, "boot=boot-1\n", ""), "gone"),
    ((0, "boot=boot-1\nproc=Z 777\n", ""), "gone"),
])
def test_a_job_that_is_not_running_at_declare_time_is_gone(reply, state):
    assert make_token("gpu1", "gpu1", 4242, runner=ScriptRunner(reply))[1] == state


def test_an_unreachable_host_at_declare_time_gives_a_token_without_identity():
    token, state = make_token("gpu1", "gpu1", 4242, runner=ScriptRunner((255, "", "ssh: connect to host")))
    assert (token, state) == ("gpu1:4242::", "unknown")


@pytest.mark.parametrize("reply, state", [
    ((0, "boot=boot-1\nproc=S 777\n", ""), "alive"),
    ((0, "boot=boot-1\nproc=S 999\n", ""), "gone"),        # the pid now belongs to another process
    ((0, "boot=boot-1\n", ""), "gone"),
    ((0, "boot=boot-2\nproc=S 777\n", ""), "lost"),        # the host rebooted
    ((255, "", "ssh: Connection timed out"), "unknown"),
    ((124, "", "timed out"), "unknown"),
])
def test_job_state_never_calls_an_unreachable_job_dead(reply, state):
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert job_state("gpu1", token, runner=ScriptRunner(reply)) == state


def test_a_token_without_identity_is_alive_while_its_pid_is():
    token = RemoteToken("gpu1", 4242, "", "")
    assert job_state("gpu1", token, runner=ScriptRunner((0, "boot=boot-9\nproc=S 1\n", ""))) == "alive"


def test_an_unreachable_host_is_backed_off_so_only_one_call_pays_the_timeout():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    runner = ScriptRunner((255, "", "ssh: connect to host gpu1: Connection timed out"))
    assert job_state("gpu1", token, runner=runner) == "unknown"
    assert job_state("gpu1", token, runner=runner) == "unknown"
    assert len(runner.calls) == 1                    # the second call was served from the backoff

    # A different host is unaffected by gpu1's backoff.
    other = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    assert job_state("gpu2", token, runner=other) == "alive"
    assert len(other.calls) == 1


def test_the_backoff_expires_and_the_host_is_asked_again(monkeypatch):
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    runner = ScriptRunner((255, "", "ssh: connect to host gpu1: Connection timed out"))
    assert job_state("gpu1", token, runner=runner) == "unknown"
    assert len(runner.calls) == 1

    real_monotonic = time.monotonic
    monkeypatch.setattr(remote_exec.time, "monotonic",
                        lambda: real_monotonic() + remote_exec.UNREACHABLE_BACKOFF + 1)
    runner2 = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    assert job_state("gpu1", token, runner=runner2) == "alive"
    assert len(runner2.calls) == 1


def test_a_successful_read_clears_a_prior_backoff():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    fail = ScriptRunner((255, "", "ssh: connect to host gpu1: Connection timed out"))
    job_state("gpu1", token, runner=fail)
    assert "gpu1" in remote_exec._unreachable_until

    remote_exec._unreachable_until["gpu1"] = 0.0    # simulate the backoff having expired
    ok = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    assert job_state("gpu1", token, runner=ok) == "alive"
    assert len(ok.calls) == 1
    assert "gpu1" not in remote_exec._unreachable_until


def test_read_identity_never_raises_on_an_invalid_ssh_target():
    # Fix B/M5 can hand a job_host with no usable ssh target down to the declare
    # path (the lease's host was removed from the pool, or its ssh value is bad).
    # ssh_argv raising ValueError there must never crash the beat.
    assert remote_exec.read_identity("", 4242, runner=ScriptRunner()) is None
    assert remote_exec.read_identity("-oProxyCommand=x", 4242, runner=ScriptRunner()) is None


def test_terminate_never_raises_on_an_invalid_ssh_target():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert remote_exec.terminate("", token, runner=ScriptRunner()) is False
    assert remote_exec.terminate("-oProxyCommand=x", token, runner=ScriptRunner()) is False


def test_collect_never_raises_on_an_invalid_ssh_target(tmp_path):
    results = remote_exec.collect("", ["~/runs/s1/work", "~/runs/s2/work"], tmp_path / "collected",
                                  runner=ScriptRunner())
    assert [r["ok"] for r in results] == [False, False]
    assert [r["path"] for r in results] == ["~/runs/s1/work", "~/runs/s2/work"]
    assert all("invalid ssh target" in r["detail"] for r in results)


def test_make_token_bypasses_the_backoff_and_clears_it_on_success():
    # A failed liveness read must not blind every later DECLARATION on the same
    # target for the whole back-off window — that job would never be signalled and
    # a reboot would never be detected.
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    fail = ScriptRunner((255, "", "ssh: connect to host gpu1: Connection timed out"))
    assert job_state("gpu1", token, runner=fail) == "unknown"
    assert "gpu1" in remote_exec._unreachable_until

    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    new_token, state = make_token("gpu1", "gpu1", 4242, runner=runner)
    assert state == "alive" and len(runner.calls) == 1     # the runner WAS called
    assert "gpu1" not in remote_exec._unreachable_until     # and success cleared the back-off

    # A later liveness read is not blinded either — it goes through.
    later = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    assert job_state("gpu1", token, runner=later) == "alive"
    assert len(later.calls) == 1


def test_an_unreadable_boot_id_never_makes_a_live_job_lost():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert job_state("gpu1", token, runner=ScriptRunner((0, "boot=\nproc=S 777\n", ""))) == "alive"


def test_an_answer_without_the_boot_label_is_unknown():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert job_state("gpu1", token, runner=ScriptRunner((0, "S 777\n", ""))) == "unknown"


def test_terminate_checks_the_start_time_and_kills_the_group():
    runner = ScriptRunner((0, "", ""))
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "boot-1"), runner=runner) is True
    command = runner.calls[0][-1]
    assert "kill -TERM" in command and "777" in command and "/proc/4242/stat" in command
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "b"),
                                 runner=ScriptRunner((255, "", "no route"))) is False


def test_terminate_reads_the_process_group_from_proc_not_ps():
    # `ps` may not exist (or may not be on PATH) on every host; /proc is always
    # there. Field 3 after stripping "pid (comm) " is pgrp — the same value
    # `ps -o pgid=` reported.
    runner = ScriptRunner((0, "", ""))
    remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "boot-1"), runner=runner)
    command = runner.calls[0][-1]
    assert "ps -o pgid" not in command and " ps " not in command
    assert 'cut -d" " -f3' in command
    assert command.count("/proc/4242/stat") == 3   # readability check, starttime read, pgrp read


def test_terminate_only_signals_a_group_its_own_launch_created():
    # setsid nohup ... & echo $! makes the job its own group leader (pid == pgid);
    # a wrong-but-live pid landing on someone else's group (a tmux server, another
    # user's notebook) must never be signaled.
    runner = ScriptRunner((0, "", ""))
    remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "boot-1"), runner=runner)
    command = runner.calls[0][-1]
    assert command.index('[ "$pg" = "4242" ] || exit 3') < command.index("kill -TERM")

    # exit 3 means "not the leader": terminate must report failure, exactly as an
    # unreachable host does.
    runner = ScriptRunner((3, "", ""))
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "boot-1"),
                                 runner=runner) is False


def test_terminate_refuses_a_token_without_a_start_time():
    runner = ScriptRunner((0, "", ""))
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "", ""), runner=runner) is False
    assert runner.calls == []


@pytest.mark.parametrize("path", ["~/runs/s1/work", "/data/runs/s1/work", "~/runs/s1/work/"])
def test_check_remote_path_accepts_a_safe_path(path):
    assert check_remote_path(path) == path.rstrip("/")


@pytest.mark.parametrize("path", ["~", "/", "~/", "~/a/../b", "~/a b", "relative/work", "$(id)", "",
                                  "~/.", "~/./", "/.", "~/a//b", "~/a/./b"])
def test_check_remote_path_refuses_anything_else(path):
    with pytest.raises(ValueError, match="collect path"):
        check_remote_path(path)


def test_collect_copies_each_path_without_deleting_and_reports_each(tmp_path):
    runner = ScriptRunner((0, "", ""), (23, "", "rsync: link_stat failed: No such file\n"))
    results = collect("gpu1", ["~/runs/s1/work", "~/runs/s1/missing", "~"], tmp_path / "collected",
                      runner=runner)
    assert [r["ok"] for r in results] == [True, False, False]
    assert "No such file" in results[1]["detail"]
    assert "collect path" in results[2]["detail"]
    assert len(runner.calls) == 2                               # the refused path never ran
    first = runner.calls[0]
    assert first[:2] == ["rsync", "-a"] and "--delete" not in first
    assert first[-2:] == ["gpu1:~/runs/s1/work", f"{tmp_path / 'collected'}/"]
    assert (tmp_path / "collected").is_dir()


def test_collect_reports_a_partial_copy_where_some_files_vanished(tmp_path):
    # rsync exit 24: some source files vanished while being transferred — the copy
    # that DID happen is still usable, so this is ok, just flagged.
    runner = ScriptRunner((24, "", "rsync warning: some files vanished before they could be "
                                   "transferred (code 24)\n"))
    results = collect("gpu1", ["~/runs/s1/work"], tmp_path / "collected", runner=runner)
    assert results[0] == {"path": "~/runs/s1/work", "ok": True,
                          "detail": "some files vanished during the copy"}


def test_collect_reports_a_failed_partial_copy(tmp_path):
    # rsync exit 23: partial transfer due to error — this one IS a failure.
    runner = ScriptRunner((23, "", "rsync: recv_generator: mkdir failed\n"
                                   "rsync error: some files/attrs were not transferred (code 23)\n"))
    results = collect("gpu1", ["~/runs/s1/work"], tmp_path / "collected", runner=runner)
    assert results[0]["ok"] is False
    assert results[0]["detail"] == "partially copied: rsync error: some files/attrs were not transferred (code 23)"


def test_collect_refuses_two_paths_with_the_same_folder_name(tmp_path):
    runner = ScriptRunner((0, "", ""))
    results = collect("gpu1", ["~/runs/s1/work", "~/runs/s2/work"], tmp_path / "collected", runner=runner)
    assert [r["ok"] for r in results] == [True, False]
    assert "folder name 'work'" in results[1]["detail"]
    assert len(runner.calls) == 1
