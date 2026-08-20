import pytest

from coscience import wiki, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus
from tests.test_wiki_beat import FakeWikiAgent


def _seed(substrate, n=1):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    for i in range(n):
        substrate.save_sprint(Sprint(id=f"s{i}", status=SprintStatus.DONE, goals="g",
                                     program="p1"))
        substrate.save_result(Result(id=f"r{i}", sprint=f"s{i}", summary="s",
                                     completed_at=float(i)))
    return p


def test_running_run_is_left_alone(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    agent.alive = True
    assert wiki.beat(substrate, p, 101.0, agent) == "wiki: running"
    assert wiki_store.load_state(substrate, "p1")["run"] is not None


def test_ok_run_records_the_batch_and_bumps_the_lint_counter(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0\n")
    agent.alive = False
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: ingest ok"
    state = wiki_store.load_state(substrate, "p1")
    assert state["run"] is None
    assert "result:r0" in state["ingested"]
    assert state["ingested"]["result:r0"]["run"] == "r0001"
    assert state["ingested"]["result:r0"]["hash"].startswith("sha256:")
    assert state["ingests_since_lint"] == 1
    assert state["failures"] == 0
    assert state["last_run"]["status"] == "ok"


def test_ingested_object_is_no_longer_pending(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    wiki.beat(substrate, p, 200.0, agent)
    assert wiki.beat(substrate, p, 300.0, agent) == ""
    assert len(agent.launches) == 1


def test_failed_run_counts_toward_quarantine(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "2")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    for i, (t_launch, t_collect) in enumerate([(100.0, 200.0), (300.0, 400.0)]):
        wiki.beat(substrate, p, t_launch, agent)
        (agent.launches[i]["run_dir"] / "agent.exit").write_text("3\n")
        agent.alive = False
        line = wiki.beat(substrate, p, t_collect, agent)
        agent.alive = True
    assert line == "wiki: ingest quarantined 1"
    state = wiki_store.load_state(substrate, "p1")
    assert state["quarantined"] == ["result:r0"]
    assert state["failures"] == 0
    assert "result:r0" not in state["ingested"]


def test_quarantined_object_is_skipped_afterwards(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "1")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("3\n")
    agent.alive = False
    wiki.beat(substrate, p, 200.0, agent)
    assert wiki.beat(substrate, p, 300.0, agent) == ""
    assert len(agent.launches) == 1


def test_dead_process_without_exit_file_waits_out_the_grace_then_fails(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    agent.alive = False                       # process gone, no agent.exit written
    assert wiki.beat(substrate, p, 130.0, agent) == "wiki: collecting"
    assert wiki_store.load_state(substrate, "p1")["run"] is not None
    assert wiki.beat(substrate, p, 400.0, agent) == "wiki: ingest failed"
    assert wiki_store.load_state(substrate, "p1")["run"] is None


def test_lint_run_ok_resets_the_counter(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "1")
    from coscience import wiki_okf
    agent = FakeWikiAgent()
    p = _seed(substrate, n=0)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 3
    wiki.beat(substrate, p, 100.0, agent)
    assert agent.launches[0]["kind"] == "lint"
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: lint ok"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 0


def test_failed_lint_run_still_owes_a_lint(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "1")
    from coscience import wiki_okf
    agent = FakeWikiAgent()
    p = _seed(substrate, n=0)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 3
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("1\n")
    agent.alive = False
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: lint failed"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 3


def test_report_counts_land_in_last_run(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0\n")
    (run_dir / "report.json").write_text(
        '{"pages_created": ["concepts/a.md", "sources/result-r0.md"],'
        ' "pages_updated": [], "notes": "two pages"}')
    agent.report = {"pages_created": ["concepts/a.md", "sources/result-r0.md"],
                    "pages_updated": [], "notes": "two pages"}
    agent.alive = False
    wiki.beat(substrate, p, 200.0, agent)
    last = wiki_store.load_state(substrate, "p1")["last_run"]
    assert last["pages_created"] == 2
    assert last["notes"] == "two pages"


def test_writes_outside_the_bundle_block_recording(substrate, monkeypatch):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    monkeypatch.setattr(wiki, "_dirty_paths",
                        lambda s: ["sprints/s0/sprint.md", "programs/p1/wiki/index.md"])
    line = wiki.beat(substrate, p, 200.0, agent)
    assert line == "wiki: ingest ESCAPED — batch not recorded"
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"] == {}
    assert state["last_run"]["escaped"] == ["sprints/s0/sprint.md"]
