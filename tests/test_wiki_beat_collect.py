import json
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
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingests_since_lint"] == 3
    assert state["failures"] == 1


def test_lint_failures_reset_the_shared_counter_so_a_fresh_ingest_gets_its_own_chances(
        substrate, monkeypatch):
    """A lint run's batch is always []. Before the fix the failure-counter reset
    lived inside `if failures >= max_failures() and batch:`, so a run of lint
    failures alone could push `failures` past threshold and leave it there
    forever (batch is empty, so the reset never fires) — meaning the very next
    ingest batch's FIRST-EVER failure would immediately inherit that debt and
    get quarantined. This drives two real failed lint beats (crossing the
    threshold via lint alone) through wiki.beat(), then a real ingest beat that
    fails once, and asserts that first ingest failure is judged on its own,
    not pre-loaded by lint's history."""
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "2")
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "1")
    from coscience import wiki_okf
    agent = FakeWikiAgent()
    p = _seed(substrate, n=1)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 3

    # Two real, consecutive failed lint runs: failures goes 0 -> 1 -> (crosses
    # threshold=2) -> reset to 0, even though batch is [] both times.
    for t_launch, t_collect in [(100.0, 200.0), (300.0, 400.0)]:
        wiki.beat(substrate, p, t_launch, agent)
        assert agent.launches[-1]["kind"] == "lint"
        (agent.launches[-1]["run_dir"] / "agent.exit").write_text("1\n")
        agent.alive = False
        line = wiki.beat(substrate, p, t_collect, agent)
        assert line == "wiki: lint failed"
        agent.alive = True
    assert wiki_store.load_state(substrate, "p1")["failures"] == 0

    # Clear the lint debt so the next launch picks the pending ingest object
    # instead of lint again (due_for_lint otherwise takes priority forever).
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 0

    # The ingest batch's own first-ever failure must NOT be quarantined.
    wiki.beat(substrate, p, 500.0, agent)
    assert agent.launches[-1]["kind"] == "ingest"
    (agent.launches[-1]["run_dir"] / "agent.exit").write_text("3\n")
    agent.alive = False
    line = wiki.beat(substrate, p, 600.0, agent)
    assert line == "wiki: ingest failed"
    state = wiki_store.load_state(substrate, "p1")
    assert state["quarantined"] == []
    assert state["failures"] == 1


def test_page_counts_are_measured_from_the_bundle_not_taken_from_the_report(substrate):
    """D2: the report names what the agent believes it wrote. r0017 claimed 4 pages
    created that already existed."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    bundle = wiki_store.bundle_dir(substrate, "p1")
    (bundle / "concepts" / "old.md").write_text("---\ntype: Concept\n---\nold\n")
    (bundle / "concepts" / "same.md").write_text("---\ntype: Concept\n---\nsame\n")
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (bundle / "concepts" / "old.md").write_text("---\ntype: Concept\n---\nrevised\n")
    (bundle / "sources" / "result-r0.md").write_text("---\ntype: Source\n---\nnew\n")
    (run_dir / "agent.exit").write_text("0\n")
    claimed = {"pages_created": ["concepts/old.md", "concepts/same.md", "sources/result-r0.md"],
               "pages_updated": ["concepts/elsewhere.md"], "notes": "n"}
    (run_dir / "report.json").write_text(json.dumps(claimed))
    agent.report = claimed
    agent.alive = False

    wiki.beat(substrate, p, 200.0, agent)

    last = wiki_store.load_state(substrate, "p1")["last_run"]
    assert (last["pages_created"], last["pages_updated"]) == (1, 1)
    assert last["notes"] == "n"


def test_a_run_without_a_page_snapshot_keeps_its_report_counts(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "pages_before.json").unlink()        # launched before snapshots existed
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


def test_writes_outside_the_bundle_are_reported_not_punished(substrate, monkeypatch):
    """Was `test_writes_outside_the_bundle_block_recording`. The batch used to be
    discarded and counted toward quarantine; B3 keeps the report and drops both,
    because the write has already happened and losing the run's pages does not
    unwrite it. `sprints/` no longer counts at all — see the sibling test."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    monkeypatch.setattr(wiki, "_dirty_paths",
                        lambda s: ["results/r9.md", "programs/p1/wiki/index.md"])
    line = wiki.beat(substrate, p, 200.0, agent)
    assert "ESCAPED" not in line
    state = wiki_store.load_state(substrate, "p1")
    assert state["last_run"]["escaped"] == ["results/r9.md"]
    assert state["ingested"], "the pages it did write are still credited"
    assert state["failures"] == 0


def _escape_cycle(substrate, p, agent, dirty, t):
    """Launch a run, then collect it as an escape. Returns the collect line.

    `dirty` is emptied for the launch beat and filled for the collect beat, since
    the guard compares the two snapshots — a path dirty in both is not an escape.
    """
    dirty["paths"] = []
    wiki.beat(substrate, p, t, agent)
    (agent.launches[-1]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    dirty["paths"] = ["results/r9.md", "programs/p1/wiki/index.md"]
    return wiki.beat(substrate, p, t + 1.0, agent)


def test_repeated_escapes_never_quarantine(substrate, monkeypatch):
    """Replaces `test_consecutive_escapes_keep_counting` and
    `test_escapes_quarantine_the_batch_at_the_threshold`.

    Counting escapes was justified by "a batch that escapes deterministically is
    relaunched every eligible beat, forever". That no longer applies: an escaped
    run now records its batch, so it does not come back. Quarantining on this
    signal is what cost p3 eight objects to writes it never made."""
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "2")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    dirty = {"paths": []}
    monkeypatch.setattr(wiki, "_dirty_paths", lambda s: list(dirty["paths"]))
    _escape_cycle(substrate, p, agent, dirty, 100.0)
    _escape_cycle(substrate, p, agent, dirty, 200.0)
    state = wiki_store.load_state(substrate, "p1")
    assert state["quarantined"] == []
    assert state["failures"] == 0


def _collect_with_report(substrate, p, agent, report_text, n=4):
    """One ok run over `n` objects whose report.json is `report_text` (or None)."""
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0\n")
    if report_text is not None:
        (run_dir / "report.json").write_text(report_text)
    agent.alive = False
    return wiki.beat(substrate, p, 200.0, agent)


def test_only_the_objects_the_report_covers_are_ingested(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate, n=4)
    line = _collect_with_report(
        substrate, p, agent, '{"objects": ["result:r0", "result:r2"]}')
    assert line == "wiki: ingest ok"
    state = wiki_store.load_state(substrate, "p1")
    assert sorted(state["ingested"]) == ["result:r0", "result:r2"]
    # The two the agent honestly skipped must come back round.
    pending = wiki_store.pending_objects(substrate, "p1", state["ingested"])
    assert sorted(o.oid for o in pending) == ["result:r1", "result:r3"]


def test_a_report_without_an_objects_key_still_ingests_the_whole_batch(substrate):
    # Spec 8.6: a missing or partial report leaves the run ok with unknown counts.
    agent = FakeWikiAgent()
    p = _seed(substrate, n=4)
    _collect_with_report(substrate, p, agent, '{"pages_created": ["concepts/a.md"]}')
    state = wiki_store.load_state(substrate, "p1")
    assert len(state["ingested"]) == 4
    assert wiki_store.pending_objects(substrate, "p1", state["ingested"]) == []


def test_the_report_cannot_ingest_objects_that_were_never_dispatched(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate, n=4)
    _collect_with_report(
        substrate, p, agent,
        '{"objects": ["result:r1", "result:nope", "sprint:s9", 17]}')
    state = wiki_store.load_state(substrate, "p1")
    assert sorted(state["ingested"]) == ["result:r1"]


def test_a_non_list_objects_field_falls_back_to_the_whole_batch(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate, n=4)
    _collect_with_report(substrate, p, agent, '{"objects": "result:r0"}')
    assert len(wiki_store.load_state(substrate, "p1")["ingested"]) == 4


def test_an_empty_objects_list_ingests_nothing_and_is_still_ok(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate, n=4)
    line = _collect_with_report(substrate, p, agent, '{"objects": []}')
    assert line == "wiki: ingest ok"
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"] == {}
    # Honest "I covered nothing" is not an error; fix 1 bounds the stuck cases.
    assert state["failures"] == 0
    assert len(wiki_store.pending_objects(substrate, "p1", state["ingested"])) == 4


# --- B3: an escape is a report, not a punishment ------------------------------

def test_a_concurrent_sprint_write_is_not_an_escape(substrate, monkeypatch):
    """The production failure, four times over. `_dirty_paths` diffs the whole
    substrate around the run, so a worker or the dispatcher writing during the
    window is indistinguishable from the wiki agent wandering. p3 r0015 was
    downgraded for `sprints/p5-c0-atom-kernel-honest-cv/work/kernel_pricing.log`
    — a different program's worker log."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    monkeypatch.setattr(wiki, "_dirty_paths", lambda s: [
        "sprints/p5-c0-atom-kernel-honest-cv/work/kernel_pricing.log",
        ".coscience/leases.json",
        "programs/p1/wiki/index.md",
    ])
    line = wiki.beat(substrate, p, 200.0, agent)

    assert "ESCAPED" not in line
    state = wiki_store.load_state(substrate, "p1")
    assert state["last_run"]["status"] == "ok"
    assert state["failures"] == 0
    assert state["ingested"], "the run's work must still be credited"


def test_a_write_into_another_programs_wiki_is_still_reported(substrate, monkeypatch):
    """Detection is kept. Only the penalty goes."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    monkeypatch.setattr(wiki, "_dirty_paths",
                        lambda s: ["programs/p2/wiki/concepts/x.md"])
    wiki.beat(substrate, p, 200.0, agent)

    state = wiki_store.load_state(substrate, "p1")
    assert state["last_run"]["escaped"] == ["programs/p2/wiki/concepts/x.md"]


def test_a_reported_escape_no_longer_discards_the_batch(substrate, monkeypatch):
    """The write already happened; throwing the run's work away does not unwrite
    it, it only loses good pages. Report, credit, move on."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    monkeypatch.setattr(wiki, "_dirty_paths",
                        lambda s: ["programs/p2/wiki/concepts/x.md"])
    wiki.beat(substrate, p, 200.0, agent)

    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"], "work is credited despite the report"
    assert state["failures"] == 0, "an escape must not push the batch to quarantine"
    assert state["quarantined"] == []


# --- B1: a rate limit is not evidence the batch is bad ------------------------

def _rate_limited_run(substrate, p, agent, t):
    """Land what a 429 actually leaves: exit 1, plus a full envelope naming it."""
    wiki.beat(substrate, p, t, agent)
    run_dir = agent.launches[-1]["run_dir"]
    (run_dir / "agent.exit").write_text("1\n")
    (run_dir / "agent.out").write_text(json.dumps(
        {"type": "result", "is_error": True, "api_error_status": 429,
         "total_cost_usd": 2.25,
         "result": "You've hit your session limit"}) + "\n")
    agent.alive = False
    line = wiki.beat(substrate, p, t + 1.0, agent)
    agent.alive = True
    return line


def test_a_rate_limited_run_does_not_count_as_a_failure(substrate):
    """The batch was fine; the box was out of budget. Counting it is what emptied
    p3's ledger — seven 429s quarantined objects whose pages were already good."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _rate_limited_run(substrate, p, agent, 100.0)

    state = wiki_store.load_state(substrate, "p1")
    assert state["failures"] == 0
    assert state["quarantined"] == []


def test_repeated_rate_limits_never_quarantine(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "2")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    for i in range(4):
        _rate_limited_run(substrate, p, agent, 100.0 + i * 10)

    state = wiki_store.load_state(substrate, "p1")
    assert state["quarantined"] == []
    assert state["failures"] == 0


def test_a_rate_limited_batch_is_still_retried(substrate):
    """Not counting must not mean forgetting: the objects stay pending so a later
    beat picks them up once there is budget."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _rate_limited_run(substrate, p, agent, 100.0)

    state = wiki_store.load_state(substrate, "p1")
    pending = wiki_store.pending_objects(substrate, "p1", state["ingested"],
                                         set(state["quarantined"]))
    assert [o.oid for o in pending] == ["result:r0"]


def test_a_rate_limited_run_is_recorded_as_deferred(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    _rate_limited_run(substrate, p, agent, 100.0)
    assert wiki_store.load_state(substrate, "p1")["last_run"]["status"] == "deferred"


def test_a_genuine_failure_still_counts(substrate):
    """The threshold still does its job for a batch that really is bad — an exit
    with no 429 in the envelope is content going wrong, not budget."""
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[-1]["run_dir"] / "agent.exit").write_text("1\n")
    agent.alive = False
    wiki.beat(substrate, p, 101.0, agent)

    assert wiki_store.load_state(substrate, "p1")["failures"] == 1


def test_a_failed_run_keeps_the_objects_it_recorded_finishing(substrate):
    """B2: a run killed at object 3 of 4 used to lose objects 1-2, whose pages were
    already written, and count the failure against them too."""
    agent = FakeWikiAgent()
    p = _seed(substrate, n=2)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    assert set(agent.launches[0]["objects"]) == {"result:r0", "result:r1"}
    (run_dir / "progress.jsonl").write_text(
        '{"object": "result:r0"}\n{"object": "result:not-in-batch"}\n{"obj')
    (run_dir / "agent.exit").write_text("3\n")
    agent.alive = False

    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: ingest failed (kept 1 of 2)"

    state = wiki_store.load_state(substrate, "p1")
    assert set(state["ingested"]) == {"result:r0"}
    assert state["failures"] == 1                   # counted against r1 alone


def test_a_failed_run_that_finished_every_object_counts_no_failure(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate, n=2)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "progress.jsonl").write_text('{"object": "result:r0"}\n{"object": "result:r1"}\n')
    (run_dir / "agent.exit").write_text("3\n")
    agent.alive = False

    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: ingest failed (kept 2 of 2)"

    state = wiki_store.load_state(substrate, "p1")
    assert set(state["ingested"]) == {"result:r0", "result:r1"}
    assert state["failures"] == 0


def test_the_ingest_prompt_asks_for_progress_per_object(substrate):
    from coscience import wiki_prompts
    p = _seed(substrate)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    text = wiki_prompts.render_ingest(p, substrate.repo_root / "bundle",
                                      [(obj, wiki_store.object_hash(obj))],
                                      substrate.repo_root / "run")
    assert "progress.jsonl" in text and '{"object": "result:r1"}' in text
