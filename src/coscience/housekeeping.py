"""Admission control for housekeeping agents — the PM reasoner and wiki runs.

Workers have had this since the resource pool existed: every sprint costs one
`workers` slot, so the number of agent processes is bounded. PM and wiki had
none. They fired on every beat, each checking only "is the 5h window below the
threshold?" — a question whose answer is blind to calls already in flight,
because a usage reading describes what has already been billed.

On 2026-09-04 that let six Claude calls start inside four minutes across three
programs. Each read the same 61% and each concluded it had 39% of headroom;
there was 39% in total. The window ended at 116% and four of the six died.

Slots rather than a spend budget, because a call cannot be priced before it runs
— one of those six cost $0.00 and still pushed the window to 109% — but it can
be counted. Global rather than per-program, because the six came from p2, p3 and
p5 and a per-program cap would have admitted all of them. Its own pool rather
than sharing `workers`, so housekeeping can never crowd out the science."""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path

from coscience import resources
from coscience.ledger import Ledger

HOUSEKEEPER_KEY = "housekeepers"

# A wiki run spans beats and its launcher can be killed between them, so a slot
# must not be held forever by a process that no longer exists. Comfortably longer
# than a real run (the worst observed was 394s) and far shorter than a 5h window.
SLOT_TTL = 3600.0

# Below the worker priority seen in live leases (55) and preemptible, so the
# dispatcher can always reclaim a slot in favour of real work.
SLOT_PRIORITY = 10


@contextlib.contextmanager
def _guard(repo_root):
    """Repo-level exclusive flock around a read-modify-write of the lease file.

    `Ledger.acquire` loads, checks capacity and saves; the PM and dispatch loops
    are separate processes, so without this two of them can both observe the same
    free slot and both take it — which is the exact failure this module exists to
    prevent. Same shape as `wiki_store.state_guard` and `artifacts._lock_guard`."""
    lockdir = Path(repo_root) / ".coscience"
    lockdir.mkdir(parents=True, exist_ok=True)
    with open(lockdir / "housekeeping.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _ledger(repo_root) -> tuple[Ledger, bool]:
    """(ledger, capped). `capped` is False when the pool declares no
    `housekeepers` key — an existing substrate stays uncapped until it opts in,
    matching how `resources.effective_requirement` treats a missing `workers`."""
    pool = resources.load_pool(repo_root)
    # Its OWN file, never `leases.json`. That one is a sprint lease space: the
    # dispatcher walks it and resolves every `sprint_id` to `sprints/<id>/sprint.md`,
    # so a `wiki:p3` entry there killed every dispatch beat in production on
    # 2026-09-04. The pool is shared (capacity carries both keys) but the keys are
    # disjoint, so separate files account correctly.
    led = Ledger(pool, Path(repo_root) / ".coscience" / "housekeeping-leases.json")
    led.load()
    return led, HOUSEKEEPER_KEY in pool.capacity


def acquire(repo_root, holder: str, now: float, ttl: float = SLOT_TTL) -> bool:
    """Take one housekeeping slot for `holder` (e.g. "wiki:p3", "pm:p2").

    True when the caller may launch. Re-acquiring an already-held slot is
    idempotent and returns True, so a beat re-entered after a restart does not
    consume a second one. Best-effort: an unreadable ledger admits the call
    rather than wedging the platform on a bookkeeping error."""
    try:
        with _guard(repo_root):
            led, capped = _ledger(repo_root)
            if not capped:
                return True
            led.expire(now)
            lease = led.acquire(holder, {HOUSEKEEPER_KEY: 1.0}, now, ttl,
                                priority=SLOT_PRIORITY, preemptible=True, platform=True)
            return lease is not None
    except (OSError, ValueError):
        return True


def release(repo_root, holder: str) -> None:
    """Give the slot back. Silent when nothing was held — callers release on every
    terminal path and should not have to know which one took a slot."""
    try:
        with _guard(repo_root):
            led, _capped = _ledger(repo_root)
            led.release(holder)
    except (OSError, ValueError):
        pass


def held(repo_root) -> list[str]:
    """Holders of a housekeeping slot right now, for the dashboard and tests."""
    try:
        led, _capped = _ledger(repo_root)
        return sorted(l.sprint_id for l in led.all_leases()
                      if HOUSEKEEPER_KEY in l.amounts)
    except (OSError, ValueError):
        return []
