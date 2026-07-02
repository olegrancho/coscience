import json

from coscience import chat_agent


def test_turn_shell_read_scope_whitelists_tools():
    cmd = chat_agent._turn_shell("claude", "hi", "read", "sid-1", resume=False,
                                 model="", out="/o", exitf="/e")
    assert "--allowedTools Read Glob Grep" in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert "--session-id sid-1" in cmd and "--resume" not in cmd


def test_turn_shell_full_scope_bypasses_and_resumes():
    cmd = chat_agent._turn_shell("claude", "hi", "full", "sid-1", resume=True,
                                 model="claude-opus-4-8", out="/o", exitf="/e")
    assert "--dangerously-skip-permissions" in cmd
    assert "--allowedTools" not in cmd
    assert "--resume sid-1" in cmd and "--session-id" not in cmd
    assert "--model claude-opus-4-8" in cmd


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
