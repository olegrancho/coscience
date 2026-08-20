"""The wiki maintenance beat: the state machine the Dispatcher runs once per
active program per cycle.

Wiki work is the least urgent consumer of the Claude budget — below sprint
execution (90) and below PM planning (80) — so it gets its own, lower threshold
and a fail-closed gate. At most one run per program is in flight at a time; that
single-writer property is what makes the agent's merge-first behaviour safe."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
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
    """Repo-relative paths git reports as changed. Best-effort: a substrate with
    no git repo yields [], which disables the containment check rather than
    blocking every run."""
    try:
        out = subprocess.run(
            ["git", "-C", str(substrate.repo_root), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    paths = []
    for line in out.splitlines():
        entry = line[3:].strip().strip('"')
        if " -> " in entry:                      # a rename: take the destination
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.append(entry)
    return paths


def _escaped(substrate, program_id: str, before: list[str], after: list[str]) -> list[str]:
    """Paths that became dirty during the run and lie outside the program's wiki.

    Runs use --dangerously-skip-permissions, so cwd is a convention rather than a
    sandbox. We detect rather than revert: reverting would risk destroying a
    concurrent sprint's legitimate work, and a wiki run is never worth that."""
    allowed = (f"programs/{program_id}/wiki/", f"programs/{program_id}/.wiki/")
    new = [p for p in after if p not in set(before)]
    return sorted(p for p in new if not p.startswith(allowed))


def _collect(substrate, program, now, agent, state, run) -> str:
    run_id, kind = run.get("id", ""), run.get("kind", "ingest")
    run_dir = wiki_store.run_dir(substrate, program.id, run_id)
    if agent.is_running(run.get("token", "")):
        return "wiki: running"

    status, report = agent.collect(run_dir)
    if status == "running":
        # The process is gone but no exit code was written: the shell was killed
        # between the two halves of the launch command. One grace window covers a
        # slow filesystem; past it the run is dead, and without this deadline the
        # program's wiki would wedge here silently forever.
        if now - float(run.get("started_at") or 0.0) < collect_grace():
            return "wiki: collecting"
        status = "failed"

    batch = list(run.get("batch") or [])
    escaped: list[str] = []
    if status == "ok":
        escaped = _escaped(substrate, program.id,
                           list(run.get("dirty_before") or []), _dirty_paths(substrate))

    state["run"] = None
    state["last_run"] = {
        "id": run_id, "kind": kind, "status": status, "at": now,
        "pages_created": len(report.get("pages_created") or []),
        "pages_updated": len(report.get("pages_updated") or []),
        "notes": str(report.get("notes") or ""),
        "escaped": escaped,
    }

    if status == "ok" and escaped:
        # The batch is deliberately NOT recorded: an agent that wrote outside its
        # bundle may equally have written the wrong thing inside it.
        state["last_run"]["status"] = "escaped"
        substrate.commit(f"wiki {program.id}: {kind} {run_id} wrote outside the bundle")
        return f"wiki: {kind} ESCAPED — batch not recorded"

    line = f"wiki: {kind} {status}"
    if status == "ok":
        objects = {o.oid: o for o in wiki_store.program_objects(substrate, program.id)}
        for oid in batch:
            obj = objects.get(oid)
            state["ingested"][oid] = {
                "hash": wiki_store.object_hash(obj) if obj else "",
                "at": now, "run": run_id}
        if kind == "ingest":
            state["ingests_since_lint"] = state.get("ingests_since_lint", 0) + 1
        else:
            state["ingests_since_lint"] = 0
            _file_lint_report(substrate, program.id, run_dir, now)
        state["failures"] = 0
    else:
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] >= max_failures() and batch:
            quarantined = list(state.get("quarantined") or [])
            quarantined += [oid for oid in batch if oid not in quarantined]
            state["quarantined"] = quarantined
            state["failures"] = 0
            line = f"wiki: {kind} quarantined {len(batch)}"

    substrate.commit(f"wiki {program.id}: {kind} {run_id} {status}")
    return line


def _file_lint_report(substrate, program_id: str, run_dir: Path, now: float) -> None:
    """Move the agent's lint summary into .wiki/lint/<date>.md."""
    from datetime import datetime, timezone
    from coscience import wiki_agent
    text = wiki_agent.read_lint_report(run_dir)
    if not text.strip():
        return
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    d = wiki_store.state_dir(substrate, program_id) / "lint"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(text)
