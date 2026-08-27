from __future__ import annotations

from coscience import wiki, wiki_okf, wiki_store
from coscience.models import Program
from tests.test_wiki_beat import FakeWikiAgent


def _bundle(substrate, policy="auto"):
    substrate.save_program(Program(id="p1", title="P1", goals="g", wiki_merge=policy))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title in (("concepts/a.md", "A"), ("concepts/b.md", "B")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title,
            body="# Definition\n\n" + "x" * 300))
    # Force the lint cadence so `beat` launches a run on a bundle that has pages
    # but no pending ingest objects — these tests are about merge handling at
    # collect, not about the lint-cadence gate itself.
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = wiki.lint_every()
    return substrate.load_program("p1")


def _finish(substrate, program, agent, report):
    """Launch a run, then hand back the report the agent 'wrote'."""
    wiki.beat(substrate, program, 1.0, agent, usage_gate=lambda: True)
    run_dir = wiki_store.run_dir(
        substrate, "p1", wiki_store.load_state(substrate, "p1")["run"]["id"])
    import json
    (run_dir / "report.json").write_text(json.dumps(report))
    (run_dir / "agent.exit").write_text("0")
    agent.alive = False
    return wiki.beat(substrate, program, 2.0, agent, usage_gate=lambda: True)


_MERGE = {"pages_created": [], "pages_updated": [], "objects": [],
          "merges": [{"winner": "concepts/a.md", "loser": "concepts/b.md",
                      "why": "same idea"}]}


def test_auto_applies_the_merge_at_collect(substrate):
    program = _bundle(substrate, "auto")
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is None
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == []


def test_the_audit_trail_names_the_commit_for_each_merge(substrate):
    import subprocess
    program = _bundle(substrate, "auto")
    subprocess.run(["git", "init", "-q", str(substrate.repo_root)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(substrate.repo_root), "config", k, v], check=True)
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    merged = wiki_store.load_state(substrate, "p1")["runs"][0]["merged"]
    assert merged[0]["loser"] == "concepts/b.md"
    assert merged[0]["winner"] == "concepts/a.md"
    assert len(merged[0]["commit"]) >= 7


def test_propose_queues_it_instead(substrate):
    program = _bundle(substrate, "propose")
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is not None
    queued = wiki_store.load_state(substrate, "p1")["merge_proposals"]
    assert len(queued) == 1
    assert queued[0]["winner"] == "concepts/a.md" and queued[0]["id"]


def test_a_refused_pair_is_never_re_proposed(substrate):
    """Without this, every lint run re-offers the same merge and the human's
    'no' is worth nothing."""
    program = _bundle(substrate, "propose")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merges_refused"] = [["concepts/a.md", "concepts/b.md"]]
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == []


def test_a_refused_pair_is_matched_in_either_direction(substrate):
    program = _bundle(substrate, "propose")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merges_refused"] = [["concepts/b.md", "concepts/a.md"]]
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == []


def test_a_pair_already_queued_is_not_re_proposed(substrate):
    """propose mode has nothing recording a pair is already pending — without
    this, every lint run appends a fresh duplicate proposal for the same pair."""
    program = _bundle(substrate, "propose")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merge_proposals"] = [{"id": "m0001", "winner": "concepts/b.md",
                                     "loser": "concepts/a.md", "why": "x",
                                     "run": "r0000", "at": 0.0}]
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == [
        {"id": "m0001", "winner": "concepts/b.md", "loser": "concepts/a.md",
         "why": "x", "run": "r0000", "at": 0.0}]


def test_a_malformed_merges_entry_is_ignored_not_raised(substrate):
    """An exit-0 run is done. A bad report is a bad page, never a crashed beat."""
    program = _bundle(substrate, "auto")
    _finish(substrate, program, FakeWikiAgent(),
            {"merges": ["not a dict", {"winner": "concepts/a.md"}, {}]})
    assert wiki_store.read_page(substrate, "p1", "concepts/a.md") is not None


def test_a_refused_merge_is_recorded_so_it_stops_being_offered(substrate):
    """A source-page proposal can never succeed. Retrying it every run is waste."""
    program = _bundle(substrate, "auto")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1"))
    _finish(substrate, program, FakeWikiAgent(),
            {"merges": [{"winner": "concepts/a.md", "loser": "sources/result-r1.md",
                         "why": "no"}]})
    refused = wiki_store.load_state(substrate, "p1")["merges_refused"]
    assert ["concepts/a.md", "sources/result-r1.md"] in [sorted(p) for p in refused]


def test_a_source_page_is_refused_under_propose_not_queued(substrate):
    """Spec 9.1: 'A proposal naming a source page is refused under both
    policies.' Queuing it under propose would show a human a choice that can
    never be accepted — it must be refused before the policy split, exactly
    like auto."""
    program = _bundle(substrate, "propose")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1"))
    _finish(substrate, program, FakeWikiAgent(),
            {"merges": [{"winner": "concepts/a.md", "loser": "sources/result-r1.md",
                         "why": "no"}]})
    state = wiki_store.load_state(substrate, "p1")
    assert state["merge_proposals"] == []
    refused = state["merges_refused"]
    assert ["concepts/a.md", "sources/result-r1.md"] in [sorted(p) for p in refused]


def test_a_source_page_is_refused_under_auto(substrate):
    """Regression guard: this already worked via the apply-time except, but
    must keep working now that the check moved ahead of the policy split."""
    program = _bundle(substrate, "auto")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1"))
    _finish(substrate, program, FakeWikiAgent(),
            {"merges": [{"winner": "concepts/a.md", "loser": "sources/result-r1.md",
                         "why": "no"}]})
    state = wiki_store.load_state(substrate, "p1")
    assert wiki_store.read_page(substrate, "p1", "sources/result-r1.md") is not None
    refused = state["merges_refused"]
    assert ["concepts/a.md", "sources/result-r1.md"] in [sorted(p) for p in refused]


def test_every_run_is_recorded_in_the_audit_trail(substrate):
    program = _bundle(substrate, "auto")
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    runs = wiki_store.load_state(substrate, "p1")["runs"]
    assert runs[0]["kind"] and runs[0]["status"] == "ok"
    assert runs[0]["merged"] == [{"loser": "concepts/b.md", "winner": "concepts/a.md",
                                  "commit": ""}]


def test_the_audit_trail_is_newest_first_and_capped(substrate):
    program = _bundle(substrate, "auto")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs"] = [{"id": f"r{i}"} for i in range(wiki.RUNS_KEPT + 5)]
    _finish(substrate, program, FakeWikiAgent(), {"merges": []})
    runs = wiki_store.load_state(substrate, "p1")["runs"]
    assert len(runs) == wiki.RUNS_KEPT
    assert runs[1]["id"] == "r0"          # the new one is at the front


def test_a_failed_run_is_still_recorded(substrate):
    """The audit trail answers 'what has this thing been doing'. A run that
    failed is part of the answer."""
    program = _bundle(substrate, "auto")
    wiki.beat(substrate, program, 1.0, FakeWikiAgent(), usage_gate=lambda: True)
    run_dir = wiki_store.run_dir(
        substrate, "p1", wiki_store.load_state(substrate, "p1")["run"]["id"])
    (run_dir / "agent.exit").write_text("1")
    agent = FakeWikiAgent()
    agent.alive = False
    wiki.beat(substrate, program, 2.0, agent, usage_gate=lambda: True)
    assert wiki_store.load_state(substrate, "p1")["runs"][0]["status"] == "failed"
