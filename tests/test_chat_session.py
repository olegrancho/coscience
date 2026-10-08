"""B3: a chat's agent stays up between replies and takes the next message on its input."""
import json
import os
import stat
import time

import pytest

from coscience import chat_agent, chat_session
from coscience.executor import is_running, terminate_detached

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
# One line per process start, so a test can count how many agents ran.
with open(os.environ["FAKE_STARTS"], "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
sid = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else sys.argv[sys.argv.index("--session-id") + 1]
for line in sys.stdin:
    msg = json.loads(line)["message"]["content"]
    print(json.dumps({"type": "system", "subtype": "init", "session_id": sid}), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "echo: " + msg.splitlines()[-1], "session_id": sid}), flush=True)
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    path = tmp_path / "claude"
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    starts = tmp_path / "starts.log"
    monkeypatch.setenv("FAKE_STARTS", str(starts))
    monkeypatch.setenv("COSCIENCE_CHAT_KEEPALIVE", "3")
    return str(path), starts


def _reply(tdir, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        text, sid, status = chat_agent.collect_turn(tdir)
        if status != "running":
            return text, status
        time.sleep(0.1)
    raise AssertionError("no reply")


def _turn(tdir, workdir, prompt, claude, scope="read", resume=False):
    return chat_agent.launch_turn(tdir, str(workdir), prompt, scope, "sid-1", resume,
                                  claude_bin=claude)


def test_the_second_message_goes_to_the_same_agent(tmp_path, fake_claude):
    claude, starts = fake_claude
    tdir = tmp_path / "thread"
    t1 = _turn(tdir, tmp_path, "hello", claude)
    assert _reply(tdir) == ("echo: hello", "ok")
    t2 = _turn(tdir, tmp_path, "again", claude, resume=True)
    assert _reply(tdir) == ("echo: again", "ok")
    assert t1 == t2 and is_running(t1)
    assert len(starts.read_text().splitlines()) == 1          # one process for both turns
    terminate_detached(t1)


def test_after_the_idle_window_it_closes_and_the_next_message_resumes(tmp_path, fake_claude):
    claude, starts = fake_claude
    tdir = tmp_path / "thread"
    t1 = _turn(tdir, tmp_path, "hello", claude)
    _reply(tdir)
    deadline = time.time() + 15
    while is_running(t1) and time.time() < deadline:
        time.sleep(0.2)
    assert not is_running(t1)
    assert chat_session.read_meta(tdir) == {}
    t2 = _turn(tdir, tmp_path, "later", claude, resume=True)
    assert _reply(tdir) == ("echo: later", "ok")
    assert t2 != t1
    assert "--resume sid-1" in starts.read_text().splitlines()[1]
    terminate_detached(t2)


def test_a_changed_scope_gets_a_fresh_agent(tmp_path, fake_claude):
    claude, starts = fake_claude
    tdir = tmp_path / "thread"
    t1 = _turn(tdir, tmp_path, "hello", claude, scope="read")
    _reply(tdir)
    t2 = _turn(tdir, tmp_path, "now edit", claude, scope="full", resume=True)
    assert _reply(tdir) == ("echo: now edit", "ok")
    assert t2 != t1 and not is_running(t1)
    assert "--dangerously-skip-permissions" in starts.read_text().splitlines()[1]
    terminate_detached(t2)


def test_switched_off_it_runs_one_process_a_turn(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("COSCIENCE_CHAT_KEEPALIVE", "0")
    claude, starts = fake_claude
    # The one-shot path runs `claude -p` with the prompt on stdin as plain text; the
    # fake speaks stream-json only, so check the command line rather than a reply.
    tdir = tmp_path / "thread"
    tok = _turn(tdir, tmp_path, json.dumps({"type": "user", "message": {"content": "x"}}), claude)
    deadline = time.time() + 10
    while not (tdir / "turn.exit").exists() and time.time() < deadline:
        time.sleep(0.1)
    assert "--input-format" not in starts.read_text()
    assert chat_session.read_meta(tdir) == {}
    terminate_detached(tok)


def test_control_files_stay_out_of_the_thread_folder(tmp_path, fake_claude):
    claude, _ = fake_claude
    tdir = tmp_path / "thread"
    tok = _turn(tdir, tmp_path, "hello", claude)
    _reply(tdir)
    assert sorted(p.name for p in tdir.iterdir()) == ["turn.exit", "turn.out"]
    terminate_detached(tok)


def test_compute_lists_the_agents_kept_up(tmp_path, fake_claude):
    from coscience.models import Program
    from coscience.service import Service
    from coscience.substrate import Substrate
    claude, _ = fake_claude
    s = Substrate(tmp_path)
    s.save_program(Program(id="p1", title="P", goals="g"))
    svc = Service(tmp_path)
    tid = svc.create_chat("p1", title="Kernel questions")["id"]
    tdir = s.chat_thread_dir("p1", tid)
    tok = _turn(tdir, tmp_path, "hello", claude)
    _reply(tdir)
    rows = svc.usage_stats()["chat_sessions"]
    assert [(r["program"], r["thread"], r["title"], r["state"]) for r in rows] == [
        ("p1", tid, "Kernel questions", "idle")]
    terminate_detached(tok)


def test_deleting_a_chat_ends_its_waiting_agent(tmp_path, fake_claude):
    from coscience.models import Program
    from coscience.service import Service
    from coscience.substrate import Substrate
    claude, _ = fake_claude
    s = Substrate(tmp_path)
    s.save_program(Program(id="p1", title="P", goals="g"))
    svc = Service(tmp_path)
    tid = svc.create_chat("p1", title="t")["id"]
    tdir = s.chat_thread_dir("p1", tid)
    tok = _turn(tdir, tmp_path, "hello", claude)
    _reply(tdir)
    svc.delete_chat("p1", tid)
    assert not is_running(tok)
    assert chat_session.read_meta(tdir) == {}


def test_a_reused_agent_is_warm_and_a_fresh_one_is_not(tmp_path, fake_claude):
    claude, _ = fake_claude
    tdir = tmp_path / "thread"
    tok = _turn(tdir, tmp_path, "hello", claude)
    _reply(tdir)
    assert not chat_session.warm(tdir)               # its first turn was a start-up
    _turn(tdir, tmp_path, "again", claude, resume=True)
    _reply(tdir)
    assert chat_session.warm(tdir)                   # the follow-up went to it running
    terminate_detached(tok)
