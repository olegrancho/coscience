import os
import stat
import time

from coscience.claude_executor import ClaudeAgent, build_instructions
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
