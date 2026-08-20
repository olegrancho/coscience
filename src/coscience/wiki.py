"""The wiki maintenance beat: the state machine the Dispatcher runs once per
active program per cycle.

Wiki work is the least urgent consumer of the Claude budget — below sprint
execution (90) and below PM planning (80) — so it gets its own, lower threshold
and a fail-closed gate. At most one run per program is in flight at a time; that
single-writer property is what makes the agent's merge-first behaviour safe."""
from __future__ import annotations

import os
from typing import Callable

from coscience import wiki_store
from coscience.models import ProgramStatus
from coscience.pause import is_paused
from coscience.worker import WEEKLY_WORKER_THRESHOLD, claude_usage_ok

WIKI_THRESHOLD = 70.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# Read at call time, not import time, so a deployment (or a test) can change them
# without a restart.
def wiki_batch() -> int:
    return _env_int("COSCIENCE_WIKI_BATCH", 4)


def lint_every() -> int:
    return _env_int("COSCIENCE_WIKI_LINT_EVERY", 5)


def max_failures() -> int:
    return _env_int("COSCIENCE_WIKI_MAX_FAILURES", 3)


def collect_grace() -> float:
    return _env_float("COSCIENCE_WIKI_COLLECT_GRACE", 60.0)


def default_usage_gate(substrate) -> Callable[[], bool]:
    """fail_open=False on purpose: an unmetered autonomous loop is exactly what
    burns a usage window unattended, and a wiki run is never urgent enough to be
    worth that risk. repo_root so the global pause is honoured first."""
    return lambda: claude_usage_ok(WIKI_THRESHOLD,
                                   weekly_threshold=WEEKLY_WORKER_THRESHOLD,
                                   fail_open=False, repo_root=substrate.repo_root)


def _next_run_id(state: dict) -> str:
    previous = ((state.get("run") or {}).get("id")
                or (state.get("last_run") or {}).get("id") or "r0000")
    try:
        n = int(str(previous).lstrip("r"))
    except ValueError:
        n = 0
    return f"r{n + 1:04d}"


def beat(substrate, program, now: float, agent, *,
         usage_gate: Callable[[], bool] | None = None) -> str:
    """One wiki beat for one program. Returns a short line for the dispatch beat
    summary, or "" when there is nothing to say."""
    if program.status != ProgramStatus.ACTIVE or not program.wiki_enabled:
        return ""

    with wiki_store.state_guard(substrate, program.id) as state:
        run = state.get("run")
        if run:
            return _collect(substrate, program, now, agent, state, run)

        if is_paused(substrate.repo_root):
            return ""
        gate = usage_gate or default_usage_gate(substrate)
        if not gate():
            return ""

        quarantined = set(state.get("quarantined") or [])
        pending = wiki_store.pending_objects(
            substrate, program.id, state.get("ingested") or {}, quarantined)
        due_for_lint = (state.get("ingests_since_lint", 0) >= lint_every()
                        and not wiki_store.is_empty(substrate, program.id))
        if not due_for_lint and not pending:
            return ""

        wiki_store.ensure_bundle(substrate, program.id)
        run_id = _next_run_id(state)
        run_dir = wiki_store.run_dir(substrate, program.id, run_id)
        bundle = wiki_store.bundle_dir(substrate, program.id)
        dirty_before = _dirty_paths(substrate)

        if due_for_lint:
            kind, batch, objects, report = "lint", [], None, _lint_report(substrate, program)
        else:
            kind, report = "ingest", ""
            chosen = pending[:wiki_batch()]
            objects = [(o, wiki_store.object_hash(o)) for o in chosen]
            batch = [o.oid for o in chosen]

        token = agent.launch(kind=kind, program=program, bundle=bundle,
                             run_dir=run_dir, objects=objects, report=report,
                             model=program.wiki_model)
        # ingests_since_lint is NOT reset here: it resets when a lint run collects
        # ok, so a lint run that fails is still owed.
        state["run"] = {"id": run_id, "kind": kind, "batch": batch, "token": token,
                        "started_at": now, "model": program.wiki_model,
                        "dirty_before": dirty_before}
        return f"wiki: launched {kind} {run_id}" + (
            f" ({len(batch)} object{'s' if len(batch) != 1 else ''})" if batch else "")


def _lint_report(substrate, program) -> str:
    """The machine lint report handed to a lint run. Task 12 replaces this stub."""
    return ""


def _dirty_paths(substrate) -> list[str]:
    """Task 10 implements the containment check; a stub keeps Task 9 runnable."""
    return []


def _collect(substrate, program, now, agent, state, run) -> str:
    """Task 10 implements this."""
    return "wiki: running"
