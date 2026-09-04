"""Reconciling the ledger against the bundle.

A run killed mid-flight leaves its pages committed but records no credit: the
agent works object by object, `_collect` only writes `state["ingested"]` when
the whole run exits ok. Reconciliation reads the provenance those pages already
carry (`origin` + `origin_hash`, the same pair `src/hash-drift` rests on) and
credits what the bundle can prove was ingested."""

from __future__ import annotations

from coscience import wiki, wiki_okf, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus


def _seed(substrate, pid="p1", rid="r1"):
    """One program with one result — the smallest thing that is an ingestable object."""
    substrate.save_program(Program(id=pid, title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id=rid, sprint="s1", summary="s", completed_at=1.0))
    wiki_store.ensure_bundle(substrate, pid)
    return f"result:{rid}"


def _live_hash(substrate, oid, pid="p1"):
    for obj in wiki_store.program_objects(substrate, pid):
        if obj.oid == oid:
            return wiki_store.object_hash(obj)
    raise AssertionError(f"no such object {oid}")


def _source_page(substrate, oid, origin_hash, pid="p1", path="sources/result-r1.md"):
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path=path, type="Source", title="Sprint s1 result", graph_excluded=True,
        body="# Summary\n\n" + "x" * 400,
        extra={"origin": oid, "origin_hash": origin_hash}))


def test_credits_an_object_whose_page_hash_matches(substrate):
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))

    report = wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    assert report["credited"] == [oid]
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"][oid]["hash"] == _live_hash(substrate, oid)
    assert state["ingested"][oid]["run"] == "reconcile"
    assert state["ingested"][oid]["at"] == 5.0


def test_credited_object_drops_out_of_quarantine(substrate):
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = [oid]

    wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    assert wiki_store.load_state(substrate, "p1")["quarantined"] == []


def test_credited_object_stops_being_pending(substrate):
    """The point of the exercise: pending_objects is what a beat re-dispatches."""
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    state = wiki_store.load_state(substrate, "p1")
    pending = wiki_store.pending_objects(substrate, "p1", state["ingested"],
                                         set(state["quarantined"]))
    assert [o.oid for o in pending] == []


def test_drift_is_reported_and_credits_nothing(substrate):
    """The page was written against content that has since moved: it needs a real
    re-ingest, and crediting it would mask that behind a stale page."""
    oid = _seed(substrate)
    _source_page(substrate, oid, "sha256:stale")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = [oid]

    report = wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    assert report["drift"] == [oid]
    assert report["credited"] == []
    state = wiki_store.load_state(substrate, "p1")
    assert oid not in state["ingested"]
    assert state["quarantined"] == [oid]


def test_object_with_no_source_page_is_left_pending(substrate):
    oid = _seed(substrate)

    report = wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    assert report["pending"] == [oid]
    assert report["credited"] == []
    assert wiki_store.load_state(substrate, "p1")["ingested"] == {}


def test_page_without_origin_hash_credits_nothing(substrate):
    """An unhashed page proves nothing about which bytes it was written from."""
    oid = _seed(substrate)
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="Sprint s1 result",
        graph_excluded=True, body="# Summary\n\n" + "x" * 400,
        extra={"origin": oid}))

    report = wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    assert report["credited"] == []
    assert report["pending"] == [oid]


def test_dry_run_is_the_default_and_writes_nothing(substrate):
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = [oid]

    report = wiki.reconcile(substrate, "p1", now=5.0)

    assert report["credited"] == [oid]          # says what it would do
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"] == {}              # and does not do it
    assert state["quarantined"] == [oid]


def test_already_credited_objects_are_not_recounted(substrate):
    """Idempotence: a second pass is a no-op, so this is safe to run after any
    crash without checking whether it already ran."""
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    report = wiki.reconcile(substrate, "p1", apply=True, now=9.0)

    assert report["credited"] == []
    assert report["already"] == [oid]
    assert wiki_store.load_state(substrate, "p1")["ingested"][oid]["at"] == 5.0


def test_a_page_may_not_credit_an_object_it_does_not_name(substrate):
    """`origin` is the binding, not the filename — a page parked at the slug of a
    different object must not launder credit onto it."""
    oid = _seed(substrate)
    _source_page(substrate, "result:something-else", _live_hash(substrate, oid))

    report = wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    assert report["credited"] == []
    assert report["pending"] == [oid]


def test_reconcile_never_touches_the_bundle(substrate):
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    before = {p: (wiki_store.bundle_dir(substrate, "p1") / p).read_text()
              for p in wiki_store.page_paths(substrate, "p1")}

    wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    after = {p: (wiki_store.bundle_dir(substrate, "p1") / p).read_text()
             for p in wiki_store.page_paths(substrate, "p1")}
    assert after == before


def test_a_credited_object_stops_consulting_its_page(substrate):
    """Deliberate boundary, found by trying to make reconcile lie on live data.

    Once the ledger's hash matches the live object, no re-ingest is owed and the
    page is not consulted again — so corrupting `origin_hash` here reports
    `already`, not `drift`. That is lint's job, not reconcile's: `src/hash-drift`
    fires on exactly this page. Reconcile owns the ledger; lint owns the page.
    Pinned because the short-circuit is surprising enough that the first
    instructions written for it were wrong."""
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    _source_page(substrate, oid, "sha256:bogus")        # the page now lies
    report = wiki.reconcile(substrate, "p1", apply=True, now=9.0)

    assert report["already"] == [oid] and report["drift"] == []


def test_drift_is_the_object_moving_not_the_page_changing(substrate):
    """The production case: the result was rewritten after its page was built, so
    the live hash leaves both the ledger and the page behind."""
    oid = _seed(substrate)
    _source_page(substrate, oid, _live_hash(substrate, oid))
    wiki.reconcile(substrate, "p1", apply=True, now=5.0)

    result_md = substrate.repo_root / "results" / "r1.md"
    result_md.write_text(result_md.read_text() + "\nrevised\n")

    report = wiki.reconcile(substrate, "p1", apply=True, now=9.0)
    assert report["drift"] == [oid] and report["credited"] == []
