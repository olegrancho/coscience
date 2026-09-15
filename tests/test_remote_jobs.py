"""O6: the worker verifies, watches, stops and collects a job on a remote host."""
import json
import time
from pathlib import Path

import pytest

from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus
from coscience.substrate import Substrate
from coscience.worker import Worker
from tests.test_worker_detached_job import FakeAgent


class Slots:
    def __init__(self, host="gpu1"):
        self.name = host

    def release(self, sprint_id):
        pass

    def acquire(self, sprint_id):
        return True

    def gpus(self, sprint_id):
        return [], None

    def host(self, sprint_id):
        return {"name": self.name, "ssh": self.name, "run_root": "~/runs",
                "facts": "Example Linux 9 · 12 threads", "notes": "nights only"}

    def ssh_for(self, host_name):
        return host_name if host_name == self.name else ""


class ScriptRunner:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[str]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        return self.replies.pop(0) if self.replies else (0, "", "")


def _queued(sub, sid="s1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1"))


def _remote_job(sprint_dir, host="gpu1"):
    (sprint_dir / "job.json").write_text(json.dumps({
        "pid": 4242, "host": host, "out_file": "~/runs/s1/work/train.out",
        "collect": ["~/runs/s1/work"], "expected_seconds": 60,
        "wake_after_seconds": 120, "max_seconds": 600, "note": "train"}))


def test_a_declared_remote_job_is_verified_and_tracked_by_its_host_token(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    w = Worker(sub, FakeAgent(on_start=_remote_job, finished=False), slots=Slots(), runner=runner)
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242:777:boot-1"
    assert (prog.job_host, prog.job_collect, prog.host) == ("gpu1", ["~/runs/s1/work"], "gpu1")
    assert prog.assess_reason == ""


def test_a_remote_job_that_is_not_running_when_declared_is_assessed_at_once(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    runner = ScriptRunner((0, "boot=boot-1\n", ""))
    w = Worker(sub, FakeAgent(on_start=_remote_job, finished=False), slots=Slots(), runner=runner)
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == "" and prog.assess_reason == "not_running"
    assert any(call[0] == "rsync" for call in runner.calls)          # outputs collected first


@pytest.mark.parametrize("host_value", [None, "local"])
def test_a_job_json_missing_host_on_a_remote_sprint_is_treated_as_that_host(tmp_path, host_value):
    sub = Substrate(tmp_path); _queued(sub)

    def on_start(d):
        payload = {"pid": 4242, "out_file": "~/runs/s1/work/train.out",
                   "collect": ["~/runs/s1/work"], "expected_seconds": 60,
                   "wake_after_seconds": 120, "max_seconds": 600, "note": "train"}
        if host_value is not None:
            payload["host"] = host_value
        (d / "job.json").write_text(json.dumps(payload))

    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    w = Worker(sub, FakeAgent(on_start=on_start, finished=False), slots=Slots(), runner=runner)
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242:777:boot-1"
    assert prog.job_host == "gpu1"
    # The identity was read over ssh — no local /proc or os.kill lookup for this pid.
    assert runner.calls and runner.calls[0][0] == "ssh"


def test_a_job_declared_on_a_host_the_sprint_does_not_hold_is_refused(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    w = Worker(sub, FakeAgent(on_start=lambda d: _remote_job(d, host="elsewhere"), finished=False),
               slots=Slots(), runner=ScriptRunner())
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == ""
    assert "elsewhere" in prog.last_error


class NudgeCapturingAgent(FakeAgent):
    """Records the nudge text of every resume() call."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.nudges: list[str] = []

    def resume(self, session_id, sprint_dir, nudge, model_slug="", repo_root=None):
        self.nudges.append(nudge)
        return super().resume(session_id, sprint_dir, nudge, model_slug, repo_root)


def test_the_agent_is_told_why_its_job_declaration_was_refused(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    agent = NudgeCapturingAgent(on_start=lambda d: _remote_job(d, host="elsewhere"), finished=False)
    w = Worker(sub, agent, slots=Slots(), runner=ScriptRunner())
    w.run_one_beat()               # launch
    w.run_one_beat()                # exit -> job refused -> resumes with a nudge
    assert agent.nudges, "the refusal should have driven a resume"
    assert "elsewhere" in agent.nudges[-1] and "gpu1" in agent.nudges[-1]


def _sleeping(sub, token="gpu1:4242:777:boot-1"):
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = token, "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = time.time(), 9e9, time.time() + 9e9
    sub.save_progress(prog)


def test_an_unreachable_host_keeps_the_job_asleep(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((255, "", "ssh: connect to host gpu1: Connection timed out"))
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242:777:boot-1" and prog.assess_reason == ""
    assert not any(call[0] == "rsync" for call in runner.calls)


def test_a_rebooted_host_wakes_the_agent_with_the_job_lost(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "boot=boot-2\n", ""))
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))
    assert sub.load_progress("s1").assess_reason == "lost"


def test_a_redeclared_job_keeps_its_identity_while_the_host_is_unreachable(tmp_path):
    # The wake branch never clears job_token (the watchdog stays armed) while an
    # assess run checks in. If that run re-declares the SAME host+pid but the host
    # happens to be unreachable right now, make_token can only return an
    # identity-less token ("gpu1:4242::") — overwriting with that would throw away
    # the real starttime/boot_id terminate() needs to ever signal this job again.
    sub = Substrate(tmp_path); _queued(sub)
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds = time.time(), 9e9
    prog.job_next_wake = time.time() - 1                 # due to wake now
    sub.save_progress(prog)
    runner = ScriptRunner(
        (0, "boot=boot-1\nproc=S 777\n", ""),             # wake: job_alive check, still alive
        (0, "", ""),                                      # wake: collect_job's rsync
        (255, "", "ssh: connect to host gpu1: Connection timed out"))  # re-declare: unreachable
    w = Worker(sub, FakeAgent(on_start=lambda d: _remote_job(d, host="gpu1"), finished=False),
               slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))       # wake -> launches the assess agent
    w.run_sprint_beat(sub.load_sprint("s1"))       # assess agent re-declares the same job
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242:777:boot-1"       # kept, not the fresh identity-less token
    assert prog.assess_reason == ""


class CapturingAgent(FakeAgent):
    """Records the context each launch received."""

    def __init__(self):
        super().__init__(finished=False)
        self.contexts = []

    def start(self, sprint, ctx, sprint_dir, repo_root=None):
        self.contexts.append(ctx)
        return super().start(sprint, ctx, sprint_dir, repo_root)


def test_outputs_are_copied_back_before_the_agent_wakes_and_it_is_told(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "boot=boot-1\n", ""), (0, "", ""))
    agent = CapturingAgent()
    w = Worker(sub, agent, slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))            # job gone -> collect -> launch the assess run
    rsync = [c for c in runner.calls if c[0] == "rsync"]
    assert rsync and rsync[0][-2] == "gpu1:~/runs/s1/work"
    assert rsync[0][-1] == f"{sub.sprint_dir('s1') / 'collected'}/"
    note = agent.contexts[-1].collect_note               # handed to the woken agent...
    assert "~/runs/s1/work" in note and "do not need to copy" in note
    assert sub.load_progress("s1").collect_note == ""    # ...and not repeated on a later run


def test_a_partial_copy_is_reported_in_the_wake_note(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "boot=boot-1\n", ""),
                          (24, "", "rsync warning: some files vanished before they could be "
                                   "transferred (code 24)\n"))
    agent = CapturingAgent()
    w = Worker(sub, agent, slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))
    note = agent.contexts[-1].collect_note
    assert "~/runs/s1/work" in note
    assert "— some files vanished during the copy" in note


def test_stopping_a_sprint_kills_its_remote_job_over_ssh(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "", ""))
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner)
    assert w.stop_sprint(sub.load_sprint("s1")) == ["s1"]
    assert "kill -TERM" in runner.calls[0][-1]
    assert sub.load_progress("s1").job_token == ""


def test_the_agent_is_told_how_to_work_on_its_host():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(host_name="gpu1", host_ssh="gpu1", host_run_dir="~/runs/s1",
                           host_facts="Example Linux 9 · 12 threads", host_notes="nights only")
    text = build_instructions(sprint, ctx, Path("/tmp/s1/scratchpad.md"))
    assert "## Where this sprint's heavy work runs" in text
    assert "ssh gpu1 'mkdir -p ~/runs/s1/work && cd ~/runs/s1 && setsid nohup" in text
    assert '"host": "gpu1"' in text and '"collect": ["~/runs/s1/work"]' in text
    assert "Example Linux 9 · 12 threads" in text and "nights only" in text


def test_the_agent_is_told_collected_is_not_kept_in_history():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(host_name="gpu1", host_ssh="gpu1", host_run_dir="~/runs/s1")
    text = build_instructions(sprint, ctx, Path("/tmp/s1/scratchpad.md"))
    assert "collected/" in text and "not kept" in text and "history" in text


def test_the_collected_folder_is_gitignored_so_it_never_enters_substrate_history(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "boot=boot-1\n", ""), (0, "", ""))
    w = Worker(sub, CapturingAgent(), slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))
    assert (sub.sprint_dir("s1") / "collected" / ".gitignore").read_text() == "*\n"


def test_a_local_sprint_gets_no_host_section():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    assert "heavy work runs" not in build_instructions(sprint, ExecutionContext(), Path("/tmp/s1/s.md"))


def test_the_wake_run_carries_the_collect_note():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(assess_reason="finished", job_out="j.out", job_note="train",
                           collect_note="Before waking you, the platform copied these paths from gpu1")
    assert "the platform copied these paths from gpu1" in build_instructions(sprint, ctx, Path("/tmp/s.md"))


def test_a_lost_job_is_explained_plainly():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(assess_reason="lost", job_out="j.out", job_note="train")
    text = build_instructions(sprint, ctx, Path("/tmp/s.md"))
    assert "the host rebooted, so your job there is gone" in text


def test_a_job_not_running_at_declare_time_is_explained_plainly():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(assess_reason="not_running", job_out="j.out", job_note="train")
    text = build_instructions(sprint, ctx, Path("/tmp/s.md"))
    assert "the job was not running when you declared it (check the pid you wrote)" in text


def test_a_timed_out_job_the_platform_could_not_stop_leaves_a_note(tmp_path):
    # A remote job declared while its host was unreachable carries a token with no
    # start time ("gpu1:4242::"); remote_exec.terminate refuses to signal such a
    # token at all. The watchdog must still end the sleep (it cannot wait forever
    # on a job it can never verify again) but must not lose that the stop failed.
    sub = Substrate(tmp_path); _queued(sub)
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242::", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = 0.0, 1.0, 9e18
    sub.save_progress(prog)
    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""),   # job_alive check: still alive
                          (0, "", ""))                            # collect's rsync
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner, usage_gate=lambda: False)
    w.run_sprint_beat(sub.load_sprint("s1"))
    assert not any("kill -TERM" in call[-1] for call in runner.calls)
    note = sub.load_progress("s1").collect_note
    assert ("The platform could not stop the job on gpu1 (the host could not be asked, the job's "
            "identity was never read, or the pid is not the leader of its own process group — "
            "launch with setsid). Check whether it is still running and stop it yourself." in note)


def test_an_unreachable_host_during_terminate_leaves_a_could_not_stop_note(tmp_path):
    # M7: unlike the identity-less-token case above, this job has a FULL token
    # (declared while the host was reachable) but the host has since gone dark —
    # terminate's own ssh call fails (255) this time, not job_alive's.
    sub = Substrate(tmp_path); _queued(sub)
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = 0.0, 1.0, 9e18
    sub.save_progress(prog)
    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""),   # job_alive check: still alive
                          (255, "", "ssh: connect to host gpu1: Connection timed out"),  # terminate fails
                          (0, "", ""))                            # collect's rsync
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner, usage_gate=lambda: False)
    w.run_sprint_beat(sub.load_sprint("s1"))
    note = sub.load_progress("s1").collect_note
    assert ("The platform could not stop the job on gpu1 (the host could not be asked, the job's "
            "identity was never read, or the pid is not the leader of its own process group — "
            "launch with setsid). Check whether it is still running and stop it yourself." in note)


def test_the_dispatcher_slot_handle_describes_the_leases_host(tmp_path, every_host_placeable):
    from coscience.dispatcher import _WorkerSlots
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"gpu1": {
        "ssh": "gpu1", "run_root": "~/runs", "notes": "nights only", "capacity": {"cpu": 8}}}})
    led = Ledger(pool, tmp_path / "leases.json"); led.load()
    led.acquire("s1", {"cpu": 4}, now=0.0, ttl=60.0, host="gpu1")
    probes = tmp_path / ".coscience" / "host-probes"; probes.mkdir(parents=True)
    (probes / "gpu1.json").write_text(json.dumps({"facts": {
        "os": "Example Linux 9", "cpu_model": "Example CPU", "threads": 12, "mem_total_kb": 65000000}}))
    slots = _WorkerSlots(led, repo_root=tmp_path)
    assert slots.host("s1") == {"name": "gpu1", "ssh": "gpu1", "run_root": "~/runs",
                                "facts": "Example Linux 9 · Example CPU · 12 threads · 62 GB memory",
                                "notes": "nights only"}
    assert slots.ssh_for("gpu1") == "gpu1" and slots.ssh_for("nope") == ""
    assert slots.host("nobody")["name"] == "local"


def test_a_lease_on_a_host_removed_from_the_pool_keeps_its_name(tmp_path, every_host_placeable):
    from coscience.dispatcher import _WorkerSlots
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"gpu1": {
        "ssh": "gpu1", "capacity": {"cpu": 8}}}})
    led = Ledger(pool, tmp_path / "leases.json"); led.load()
    led.acquire("s1", {"cpu": 4}, now=0.0, ttl=60.0, host="gpu1")
    # gpu1 is now gone from the pool (edited out of resources.yaml since the grant).
    smaller = ResourcePool.from_dict({"cpu": 1})
    led.pool = smaller
    slots = _WorkerSlots(led, repo_root=tmp_path)
    assert slots.host("s1") == {"name": "gpu1", "ssh": "", "run_root": "", "facts": "", "notes": ""}


def test_a_declaration_on_a_host_with_no_ssh_target_does_not_crash_the_beat(tmp_path, every_host_placeable):
    # M5 + Fix B: a sprint whose lease is on a host removed from the pool (so
    # ssh_for() returns "") still gets `held` = that host's name, and a job.json
    # naming no host is rewritten onto it. make_token must not be asked to build an
    # ssh_argv for "" — the beat must complete with an identity-less token, not raise.
    from coscience.dispatcher import _WorkerSlots
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    sub = Substrate(tmp_path); _queued(sub)
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"gpu1": {
        "ssh": "gpu1", "capacity": {"cpu": 8}}}})
    led = Ledger(pool, tmp_path / "leases.json"); led.load()
    led.acquire("s1", {"cpu": 1}, now=0.0, ttl=60.0, host="gpu1")
    led.pool = ResourcePool.from_dict({"cpu": 1})    # gpu1 removed from the pool since the grant
    slots = _WorkerSlots(led, repo_root=tmp_path)

    def on_start(d):
        (d / "job.json").write_text(json.dumps({"pid": 4242, "out_file": "o.out", "note": "n"}))

    runner = ScriptRunner()
    w = Worker(sub, FakeAgent(on_start=on_start, finished=False), slots=slots, runner=runner)
    w.run_one_beat()
    w.run_one_beat()             # must not raise
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242::"
    assert prog.job_host == "gpu1"
    assert runner.calls == []    # ssh was never actually attempted


def test_ssh_for_never_hands_out_a_target_ssh_argv_would_reject(tmp_path):
    # resources.py refuses a bad `ssh:` at parse time, but this is defence in depth:
    # a Host built any other way (or a future relaxed parse) must still never reach
    # a beat as a usable ssh target — ssh_for degrades it to "" ("unknown") instead
    # of letting host_probe.ssh_argv raise inside the beat (Fix C).
    from coscience.dispatcher import _WorkerSlots
    from coscience.ledger import Ledger
    from coscience.resources import Host, ResourcePool
    pool = ResourcePool(capacity={}, hosts=[Host("bad", ssh="-oProxyCommand=x")])
    led = Ledger(pool, tmp_path / "leases.json"); led.load()
    slots = _WorkerSlots(led, repo_root=tmp_path)
    assert slots.ssh_for("bad") == ""
