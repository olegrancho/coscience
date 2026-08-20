from pathlib import Path

import pytest

from coscience import wiki, wiki_store
from coscience.models import Program, ProgramStatus, Result, Sprint, SprintStatus


class FakeWikiAgent:
    """Records launches; never starts a process."""

    def __init__(self):
        self.launches = []
        self.alive = True
        self.exit_code = 0

    def launch(self, *, kind, program, bundle, run_dir, objects=None, report="",
               model=""):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.launches.append({"kind": kind, "objects": [o.oid for o, _ in (objects or [])],
                              "model": model, "run_dir": run_dir, "report": report})
        return f"tok{len(self.launches)}"

    def is_running(self, token):
        return self.alive

    def collect(self, run_dir):
        return ("ok" if self.exit_code == 0 else "failed"), {}


@pytest.fixture
def agent2():
    return FakeWikiAgent()


def _seed_result(substrate, rid, sid, pid, at):
    substrate.save_sprint(Sprint(id=sid, status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id=rid, sprint=sid, summary=f"s {rid}", completed_at=at))


def test_disabled_program_does_nothing(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", wiki_enabled=False)
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_non_active_program_does_nothing(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", status=ProgramStatus.PAUSED)
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_nothing_pending_returns_empty(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_launch_ingests_the_oldest_batch_and_records_the_run(substrate, agent2, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "2")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    for i, at in enumerate([30.0, 10.0, 20.0]):
        _seed_result(substrate, f"r{i}", f"s{i}", "p1", at)
    line = wiki.beat(substrate, p, 100.0, agent2)
    assert line.startswith("wiki: launched ingest")
    assert agent2.launches[0]["objects"] == ["result:r1", "result:r2"]  # oldest first
    state = wiki_store.load_state(substrate, "p1")
    assert state["run"]["kind"] == "ingest"
    assert state["run"]["batch"] == ["result:r1", "result:r2"]
    assert state["run"]["token"] == "tok1"
    assert state["run"]["started_at"] == 100.0


def test_launch_creates_the_bundle(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    assert (wiki_store.bundle_dir(substrate, "p1") / "CLAUDE.md").is_file()


def test_launch_uses_the_programs_wiki_model(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", wiki_model="claude-haiku-4-5-20251001")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    assert agent2.launches[0]["model"] == "claude-haiku-4-5-20251001"


def test_only_one_run_at_a_time(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    agent2.alive = True
    assert wiki.beat(substrate, p, 110.0, agent2) == "wiki: running"
    assert len(agent2.launches) == 1


def test_usage_gate_blocks_launch(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2, usage_gate=lambda: False) == ""
    assert agent2.launches == []


def test_pause_blocks_launch(substrate, agent2):
    from coscience import pause
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    pause.set_paused(substrate.repo_root, True)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_lint_runs_after_the_cadence_and_only_on_a_non_empty_bundle(substrate, agent2,
                                                                    monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "2")
    from coscience import wiki_okf
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    wiki_store.ensure_bundle(substrate, "p1")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 2
    # empty bundle: nothing to lint, and nothing pending either
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="# Definition\n\nx\n"))
    line = wiki.beat(substrate, p, 110.0, agent2)
    assert line.startswith("wiki: launched lint")
    assert agent2.launches[-1]["kind"] == "lint"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 2  # not reset yet


def test_run_ids_increment(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    first = wiki_store.load_state(substrate, "p1")["run"]["id"]
    assert first == "r0001"
    assert (wiki_store.state_dir(substrate, "p1") / "runs" / first).is_dir()
