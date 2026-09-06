"""Admission control for housekeeping agents (PM and wiki).

On 2026-09-04 six Claude calls started inside four minutes — two wiki ingests,
two PM cycles and a worker — and drove the 5h window from 26% to 116%, killing
four of them. Each had checked the budget and each had seen room, because a
usage reading only describes calls that have already been billed and is blind to
work in flight. Workers were never the problem: they already hold `workers`
leases. PM and wiki had no admission control at all.

These pin the slot pool that closes that gap. Slots rather than a cost budget,
because one of those six calls cost $0.00 and still pushed the window to 109% —
a call cannot be priced before it runs, but it can be counted."""

from __future__ import annotations

from coscience import housekeeping


def _pool(tmp_path, **caps):
    (tmp_path / ".coscience").mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}: {v}" for k, v in caps.items())
    (tmp_path / ".coscience" / "resources.yaml").write_text(body + "\n")
    return tmp_path


def test_a_slot_is_granted_when_the_pool_has_room(tmp_path):
    _pool(tmp_path, workers=1.0, housekeepers=1.0)
    assert housekeeping.acquire(tmp_path, "wiki:p3", now=100.0) is True
    assert housekeeping.held(tmp_path) == ["wiki:p3"]


def test_a_second_holder_is_refused_while_the_only_slot_is_taken(tmp_path):
    """The 09-04 failure in miniature: the second launcher must be told no."""
    _pool(tmp_path, housekeepers=1.0)
    assert housekeeping.acquire(tmp_path, "wiki:p3", now=100.0) is True
    assert housekeeping.acquire(tmp_path, "wiki:p5", now=100.0) is False
    assert housekeeping.held(tmp_path) == ["wiki:p3"]


def test_releasing_frees_the_slot_for_the_next_holder(tmp_path):
    _pool(tmp_path, housekeepers=1.0)
    housekeeping.acquire(tmp_path, "wiki:p3", now=100.0)
    housekeeping.release(tmp_path, "wiki:p3")
    assert housekeeping.acquire(tmp_path, "pm:p2", now=101.0) is True


def test_the_cap_is_global_not_per_program(tmp_path):
    """The six calls came from p2, p3 and p5 — a per-program cap would have let
    every one of them through."""
    _pool(tmp_path, housekeepers=2.0)
    assert housekeeping.acquire(tmp_path, "wiki:p3", now=100.0) is True
    assert housekeeping.acquire(tmp_path, "pm:p5", now=100.0) is True
    assert housekeeping.acquire(tmp_path, "wiki:p2", now=100.0) is False


def test_housekeeping_does_not_consume_worker_capacity(tmp_path):
    """Its own pool: housekeeping must never crowd out the actual science."""
    _pool(tmp_path, workers=1.0, housekeepers=1.0)
    housekeeping.acquire(tmp_path, "wiki:p3", now=100.0)

    from coscience import resources
    from coscience.ledger import Ledger
    led = Ledger(resources.load_pool(tmp_path), tmp_path / ".coscience" / "leases.json")
    led.load()
    assert led.available()["workers"] == 1.0      # untouched by the wiki lease


def test_a_pool_that_declares_no_housekeepers_is_uncapped(tmp_path):
    """Matches how `effective_requirement` treats a missing `workers` key, so an
    existing substrate keeps behaving exactly as before until it opts in."""
    _pool(tmp_path, workers=1.0)
    for holder in ("wiki:p3", "wiki:p5", "pm:p2", "pm:p5"):
        assert housekeeping.acquire(tmp_path, holder, now=100.0) is True


def test_a_slot_expires_so_a_killed_process_cannot_hold_it_forever(tmp_path):
    """A wiki run spans beats and its holder can be SIGKILLed between them. Without
    a TTL the pool would wedge and no housekeeping would ever run again."""
    _pool(tmp_path, housekeepers=1.0)
    housekeeping.acquire(tmp_path, "wiki:p3", now=100.0, ttl=60.0)
    assert housekeeping.acquire(tmp_path, "wiki:p5", now=130.0) is False
    assert housekeeping.acquire(tmp_path, "wiki:p5", now=100_000.0) is True


def test_reacquiring_the_same_holder_is_idempotent(tmp_path):
    """A beat that re-enters after a restart must not consume a second slot."""
    _pool(tmp_path, housekeepers=1.0)
    assert housekeeping.acquire(tmp_path, "wiki:p3", now=100.0) is True
    assert housekeeping.acquire(tmp_path, "wiki:p3", now=105.0) is True
    assert housekeeping.held(tmp_path) == ["wiki:p3"]


def test_releasing_something_never_held_is_harmless(tmp_path):
    _pool(tmp_path, housekeepers=1.0)
    housekeeping.release(tmp_path, "wiki:nope")          # must not raise


def test_the_lease_is_low_priority_and_preemptible(tmp_path):
    """So the dispatcher's own preemption logic can always reclaim from
    housekeeping in favour of real work."""
    _pool(tmp_path, housekeepers=1.0)
    housekeeping.acquire(tmp_path, "wiki:p3", now=100.0)

    from coscience import resources
    from coscience.ledger import Ledger
    led = Ledger(resources.load_pool(tmp_path),
                 tmp_path / ".coscience" / "housekeeping-leases.json")
    led.load()
    lease = led.lease_for("wiki:p3")
    assert lease.preemptible is True
    assert lease.priority < 55        # below the worker priority seen in leases.json


def test_housekeeping_leases_never_enter_the_sprint_lease_file(tmp_path):
    """Regression, found in production 2026-09-04.

    `leases.json` is a SPRINT lease space: the dispatcher walks it and calls
    `is_yieldable(lease.sprint_id)`, which loads `sprints/<id>/sprint.md`. A
    `wiki:p3` lease sitting there made every dispatch beat die with
    `No such file: sprints/wiki:p3/sprint.md`. Housekeeping keeps its own file."""
    import json
    _pool(tmp_path, workers=1.0, housekeepers=1.0)
    housekeeping.acquire(tmp_path, "wiki:p3", now=100.0)

    sprint_leases = tmp_path / ".coscience" / "leases.json"
    if sprint_leases.is_file():
        ids = [l["sprint_id"] for l in json.loads(sprint_leases.read_text())]
        assert "wiki:p3" not in ids
    assert housekeeping.held(tmp_path) == ["wiki:p3"]
