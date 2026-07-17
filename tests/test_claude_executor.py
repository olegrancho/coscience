import json
import os
import stat
import time

from coscience.claude_executor import ClaudeAgent, build_instructions, read_activity
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus


def _sprint():
    return Sprint(id="sp1", status=SprintStatus.APPROVED, goals="report the min gap",
                  plan=["scan primes above 1e6", "tabulate the gaps"],
                  title="Baseline scan", summary="scan consecutive primes")


def test_instructions_carry_goal_steps_prior_and_autonomy(tmp_path):
    ctx = ExecutionContext(program_title="Demo",
                           program_goal="find the smallest prime gap above 1e6",
                           prior_results=["## Earlier\nmin_gap was 2"])
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    for needle in ("find the smallest prime gap above 1e6", "Baseline scan",
                   "scan primes above 1e6", "min_gap was 2",
                   "scratchpad", "usage", "autonomous"):
        assert needle in text
    # guidance, not commands
    assert "python3 -c" not in text


def test_instructions_carry_human_comments(tmp_path):
    ctx = ExecutionContext(human_comments=["double-check the boundary at exactly 1e6"])
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    assert "Human feedback" in text
    assert "double-check the boundary at exactly 1e6" in text


def test_start_writes_instructions_and_runs_to_a_result(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/usr/bin/env bash\necho AGENT RAN\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    sprint_dir = tmp_path / "sprints" / "sp1"
    agent = ClaudeAgent(claude_bin=str(fake))
    token = agent.start(_sprint(), None, sprint_dir, repo_root=tmp_path)
    assert (sprint_dir / "instructions.md").is_file()

    deadline = time.time() + 5
    while agent.is_running(token) and time.time() < deadline:
        time.sleep(0.05)
    text, status = agent.collect(sprint_dir)
    assert status == "ok"
    assert "AGENT RAN" in text


def test_collect_reports_interrupted_when_no_exit_sentinel(tmp_path):
    sprint_dir = tmp_path / "sprints" / "sp1"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "agent.out").write_text("partial work")    # no agent.exit -> killed
    text, status = ClaudeAgent().collect(sprint_dir)
    assert status == "interrupted"
    assert text == "partial work"


def test_collect_reports_failed_on_nonzero_exit(tmp_path):
    sprint_dir = tmp_path / "sprints" / "sp1"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "agent.out").write_text("boom: ImportError")
    (sprint_dir / "agent.exit").write_text("1\n")
    text, status = ClaudeAgent().collect(sprint_dir)
    assert status == "failed"
    assert "ImportError" in text


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_collect_unwraps_stream_result_and_writes_cost(tmp_path):
    sprint_dir = tmp_path / "sprints" / "sp1"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "agent.out").write_text(_stream(
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                       "input": {"command": "ls"}}]}},
        {"type": "result", "subtype": "success", "result": "min gap is 2",
         "total_cost_usd": 0.42, "num_turns": 7,
         "usage": {"input_tokens": 1000, "output_tokens": 200}},
    ))
    (sprint_dir / "agent.exit").write_text("0\n")
    text, status = ClaudeAgent().collect(sprint_dir)
    assert status == "ok"
    assert text == "min gap is 2"                         # final message, not the JSONL
    cost = json.loads((sprint_dir / "agent.cost.json").read_text())
    assert cost["cost"] == 0.42 and cost["tokens"] == 1200


def test_collect_keeps_raw_when_no_result_event(tmp_path):
    # A usage-limit message instead of a stream must survive for limit detection.
    sprint_dir = tmp_path / "sprints" / "sp1"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "agent.out").write_text("You've hit your session limit · resets 6:40am")
    (sprint_dir / "agent.exit").write_text("1\n")
    text, status = ClaudeAgent().collect(sprint_dir)
    assert status == "failed" and "session limit" in text


def test_read_activity_labels_current_action(tmp_path):
    sprint_dir = tmp_path / "sprints" / "sp1"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "agent.out").write_text(_stream(
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write",
                                                       "input": {"file_path": "/x/scratchpad.md"}}]}},
    ))
    act = read_activity(sprint_dir, now=time.time())
    assert act["label"] == "using Write · scratchpad.md"
    assert act["active"] is True                          # just written
    # an old feed reads as inactive (process gone)
    stale = read_activity(sprint_dir, now=time.time() + 10_000)
    assert stale["active"] is False


def test_read_activity_none_without_feed(tmp_path):
    sprint_dir = tmp_path / "sprints" / "sp1"
    sprint_dir.mkdir(parents=True)
    assert read_activity(sprint_dir) is None


def test_start_disables_background_tasks_and_monitor(tmp_path, monkeypatch):
    # Option-2 hardening: the launch must strip the session-bound background paths so
    # the ONLY way to outlive a turn is the OS-level detached-job protocol.
    captured = {}
    monkeypatch.setattr("coscience.claude_executor.launch_detached",
                        lambda cmd, cwd=None: captured.update(cmd=cmd, cwd=cwd) or "tok")
    agent = ClaudeAgent(claude_bin="claude")
    token = agent.start(_sprint(), None, tmp_path / "sp1", repo_root=tmp_path)
    assert token == "tok"
    assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 claude -p" in captured["cmd"]
    assert "--disallowedTools Monitor" in captured["cmd"]


def test_resume_builds_resume_command_and_clears_prior_capture(tmp_path, monkeypatch):
    sprint_dir = tmp_path / "sp1"
    sprint_dir.mkdir()
    (sprint_dir / "agent.out").write_text("old feed")
    (sprint_dir / "agent.exit").write_text("0")
    captured = {}
    monkeypatch.setattr("coscience.claude_executor.launch_detached",
                        lambda cmd, cwd=None: captured.update(cmd=cmd) or "tok2")
    agent = ClaudeAgent(claude_bin="claude")
    tok = agent.resume("sess-123", sprint_dir, "did you finish?",
                       model_slug="claude-x", repo_root=tmp_path)
    assert tok == "tok2"
    c = captured["cmd"]
    assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1" in c
    assert "--resume sess-123" in c and "--disallowedTools Monitor" in c
    assert "--model claude-x" in c and "did you finish?" in c
    assert not (sprint_dir / "agent.out").exists()      # prior capture cleared
    assert not (sprint_dir / "agent.exit").exists()


def test_read_session_id_from_stream(tmp_path):
    sprint_dir = tmp_path / "sp1"
    sprint_dir.mkdir()
    (sprint_dir / "agent.out").write_text(_stream(
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {"type": "assistant", "session_id": "abc-123", "message": {"content": []}},
    ))
    assert ClaudeAgent.read_session_id(sprint_dir) == "abc-123"


def test_read_session_id_absent(tmp_path):
    sprint_dir = tmp_path / "sp1"
    sprint_dir.mkdir()
    (sprint_dir / "agent.out").write_text("no json here")
    assert ClaudeAgent.read_session_id(sprint_dir) == ""


def test_instructions_require_finished_json_signal(tmp_path):
    text = build_instructions(_sprint(), None, tmp_path / "scratchpad.md")
    assert "finished.json" in text
    assert "DETACHED-JOB PROTOCOL" in text
    assert "not available to you" in text           # background tooling declared disabled
