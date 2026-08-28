import json
import os
import tempfile
import time

from coscience import chat_agent


def test_turn_shell_read_scope_whitelists_tools():
    cmd = chat_agent._turn_shell("claude", "/p", "read", "sid-1", resume=False,
                                 model="", out="/o", exitf="/e")
    assert "--allowedTools Read Glob Grep" in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert "--session-id sid-1" in cmd and "--resume" not in cmd


def test_turn_shell_full_scope_bypasses_and_resumes():
    cmd = chat_agent._turn_shell("claude", "/p", "full", "sid-1", resume=True,
                                 model="claude-opus-4-8", out="/o", exitf="/e")
    assert "--dangerously-skip-permissions" in cmd
    assert "--allowedTools" not in cmd
    assert "--resume sid-1" in cmd and "--session-id" not in cmd
    assert "--model claude-opus-4-8" in cmd


def test_turn_shell_feeds_prompt_on_stdin_not_argv():
    """The prompt must never become an argv word — see _turn_shell's docstring."""
    cmd = chat_agent._turn_shell("claude", "/tmp/x.prompt", "read", "sid-1",
                                 resume=False, model="", out="/o", exitf="/e")
    assert "< /tmp/x.prompt" in cmd
    assert cmd.index("< /tmp/x.prompt") < cmd.index("> /o")
    assert cmd.strip().endswith("rm -f /tmp/x.prompt")


def _stub_claude(tmp_path):
    """A stand-in for the claude binary that ignores its flags and echoes stdin."""
    stub = tmp_path / "claude-stub"
    stub.write_text("#!/bin/sh" + chr(10) + "cat" + chr(10))
    stub.chmod(0o755)
    return str(stub)


def test_launch_turn_delivers_a_prompt_larger_than_max_arg_strlen(tmp_path):
    """A first-turn prompt carries the whole program preamble and can exceed the
    128 KiB Linux per-argument cap; passing it as an argv word raised E2BIG."""
    prompt = "x" * 200_000                      # > MAX_ARG_STRLEN (131072)
    thread = tmp_path / "thread"
    chat_agent.launch_turn(thread_dir=thread, workdir=str(tmp_path), prompt=prompt,
                           scope="read", session_id="sid-1", resume=False,
                           claude_bin=_stub_claude(tmp_path))
    deadline = time.time() + 10
    while not (thread / "turn.exit").exists() and time.time() < deadline:
        time.sleep(0.05)
    assert (thread / "turn.exit").read_text().strip() == "0"
    assert (thread / "turn.out").read_text() == prompt


def test_launch_turn_removes_the_staged_prompt_file(tmp_path, monkeypatch):
    """The shell line rm's the file after the turn exits, so nothing accumulates."""
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(staging))
    thread = tmp_path / "thread"
    chat_agent.launch_turn(thread_dir=thread, workdir=str(tmp_path), prompt="hi",
                           scope="read", session_id="sid-1", resume=False,
                           claude_bin=_stub_claude(tmp_path))
    deadline = time.time() + 10
    while os.listdir(staging) and time.time() < deadline:
        time.sleep(0.05)
    assert os.listdir(staging) == []


def test_launch_turn_does_not_stage_the_prompt_inside_the_thread_dir(tmp_path):
    """The substrate is committed with `git add -A` while the turn still runs, so a
    prompt staged next to turn.out would land in substrate history every first turn."""
    prompt = "unique-preamble-marker " * 5000
    thread = tmp_path / "thread"
    chat_agent.launch_turn(thread_dir=thread, workdir=str(tmp_path), prompt=prompt,
                           scope="read", session_id="sid-1", resume=False,
                           claude_bin=_stub_claude(tmp_path))
    assert set(os.listdir(thread)) <= {"turn.out", "turn.exit"}


def test_collect_turn_unwraps_result_and_session(tmp_path):
    (tmp_path / "turn.out").write_text(
        json.dumps({"type": "assistant"}) + "\n"
        + json.dumps({"type": "result", "result": "here you go", "session_id": "sess-9"}) + "\n")
    (tmp_path / "turn.exit").write_text("0")
    text, sid, status = chat_agent.collect_turn(tmp_path)
    assert (text, sid, status) == ("here you go", "sess-9", "ok")


def test_collect_turn_running_until_exit(tmp_path):
    (tmp_path / "turn.out").write_text("{}\n")
    assert chat_agent.collect_turn(tmp_path)[2] == "running"
