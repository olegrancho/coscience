import json
from pathlib import Path

import pytest

from coscience import wiki_agent, wiki_store
from coscience.models import Program

PROGRAM = Program(id="p1", title="P1", goals="g")
OBJ = (wiki_store.WikiObject(oid="result:r1", kind="result", title="R1",
                             paths=[Path("/repo/results/r1.md")],
                             resource="/results/r1.md",
                             slug="sources/result-r1.md"), "sha256:aaa")


@pytest.fixture
def captured(monkeypatch):
    calls = {}

    def fake_launch(command, cwd=None):
        calls["command"] = command
        calls["cwd"] = cwd
        return "4242:99"
    monkeypatch.setattr(wiki_agent.executor, "launch_detached", fake_launch)
    return calls


def test_launch_writes_instructions_and_returns_a_token(tmp_path, captured):
    run = tmp_path / "runs" / "r0001"
    token = wiki_agent.WikiAgent().launch(
        kind="ingest", program=PROGRAM, bundle=tmp_path / "wiki", run_dir=run,
        objects=[OBJ], model="claude-sonnet-5")
    assert token == "4242:99"
    text = (run / "instructions.md").read_text()
    assert "result:r1" in text
    assert "sha256:aaa" in text


def test_launch_runs_in_the_bundle_with_the_carried_over_flags(tmp_path, captured):
    run = tmp_path / "runs" / "r0001"
    wiki_agent.WikiAgent().launch(kind="ingest", program=PROGRAM,
                                  bundle=tmp_path / "wiki", run_dir=run,
                                  objects=[OBJ], model="claude-haiku-4-5-20251001")
    cmd = captured["command"]
    assert captured["cwd"] == tmp_path / "wiki"
    assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1" in cmd
    assert "--disallowedTools Monitor" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format stream-json --verbose" in cmd
    assert "--model claude-haiku-4-5-20251001" in cmd
    assert str(run / "agent.out") in cmd
    assert str(run / "agent.exit") in cmd


def test_launch_clears_a_previous_runs_leftovers(tmp_path, captured):
    run = tmp_path / "runs" / "r0001"
    run.mkdir(parents=True)
    (run / "agent.exit").write_text("1\n")
    (run / "report.json").write_text("{}")
    wiki_agent.WikiAgent().launch(kind="ingest", program=PROGRAM,
                                  bundle=tmp_path / "wiki", run_dir=run, objects=[OBJ])
    assert not (run / "agent.exit").exists()
    assert not (run / "report.json").exists()


def test_collect_running_when_no_exit_file(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    assert wiki_agent.WikiAgent().collect(run) == ("running", {})


def test_collect_ok_with_report(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    (run / "report.json").write_text(json.dumps(
        {"pages_created": ["concepts/a.md"], "objects": ["result:r1"]}))
    status, report = wiki_agent.WikiAgent().collect(run)
    assert status == "ok"
    assert report["pages_created"] == ["concepts/a.md"]


def test_collect_ok_without_report_is_still_ok(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    assert wiki_agent.WikiAgent().collect(run) == ("ok", {})


def test_collect_failed_on_nonzero_exit(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("2\n")
    assert wiki_agent.WikiAgent().collect(run)[0] == "failed"


def test_collect_tolerates_a_corrupt_report(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    (run / "report.json").write_text("{oops")
    assert wiki_agent.WikiAgent().collect(run) == ("ok", {})


def test_collect_lint_run_report_text_is_available(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    (run / "lint-report.md").write_text("# what I changed\n")
    assert "what I changed" in wiki_agent.read_lint_report(run)
