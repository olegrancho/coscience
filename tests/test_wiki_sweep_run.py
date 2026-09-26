"""L9: the sweep is a wiki run kind — requested or every N lints, never mid-queue."""
from coscience import wiki, wiki_okf, wiki_store
from tests.test_wiki_beat import FakeWikiAgent
from tests.test_wiki_beat_collect import _seed


def _caught_up(substrate, p, agent, now=100.0):
    """Ingest the seeded result so nothing is pending, and leave one page behind."""
    wiki.beat(substrate, p, now, agent)
    (agent.launches[-1]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    wiki.beat(substrate, p, now + 1, agent)
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x" * 400))


def test_a_requested_sweep_launches_once_the_queue_is_empty(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _caught_up(substrate, p, agent)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["sweep_requested"] = True
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: launched sweep r0002"
    assert agent.launches[-1]["kind"] == "sweep"
    state = wiki_store.load_state(substrate, "p1")
    assert "sweep_requested" not in state and state["run"]["kind"] == "sweep"

    run_dir = agent.launches[-1]["run_dir"]
    (run_dir / "sweep-report.md").write_text("| page | kind |\n")
    (run_dir / "agent.exit").write_text("0\n")
    assert wiki.beat(substrate, p, 300.0, agent) == "wiki: sweep ok"
    assert wiki_store.load_state(substrate, "p1")["runs_since_sweep"] == 0
    filed = list((wiki_store.state_dir(substrate, "p1") / "lint").glob("*-sweep.md"))
    assert len(filed) == 1


def test_no_sweep_without_a_request_or_a_schedule(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _caught_up(substrate, p, agent)
    assert wiki.beat(substrate, p, 200.0, agent) == ""


def test_a_scheduled_sweep_follows_enough_runs(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_SWEEP_EVERY", "2")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _caught_up(substrate, p, agent)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs_since_sweep"] = 1
    assert wiki.beat(substrate, p, 200.0, agent) == ""
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs_since_sweep"] = 2
    assert wiki.beat(substrate, p, 300.0, agent).startswith("wiki: launched sweep")


def test_a_pending_object_goes_before_a_requested_sweep(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["sweep_requested"] = True
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x" * 400))
    assert "ingest" in wiki.beat(substrate, p, 100.0, agent)
    assert wiki_store.load_state(substrate, "p1")["sweep_requested"] is True


def test_by_default_a_sweep_comes_every_ten_runs(substrate, monkeypatch):
    monkeypatch.delenv("COSCIENCE_WIKI_SWEEP_EVERY", raising=False)
    assert wiki.sweep_every() == 10
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _caught_up(substrate, p, agent)
    assert wiki_store.load_state(substrate, "p1")["runs_since_sweep"] == 1   # the ingest counted
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs_since_sweep"] = 10
    assert wiki.beat(substrate, p, 200.0, agent).startswith("wiki: launched sweep")


def test_zero_turns_the_schedule_off(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_SWEEP_EVERY", "0")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _caught_up(substrate, p, agent)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs_since_sweep"] = 50
    assert wiki.beat(substrate, p, 200.0, agent) == ""
