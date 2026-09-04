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
RUNS_KEPT = 500


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
         usage_gate: Callable[[], bool] | None = None,
         forced_by: str | None = None) -> str:
    """One wiki beat for one program. Returns a short line for the dispatch beat
    summary, or "" when there is nothing to say.

    `forced_by` names the human who pressed the button, and is recorded on the
    run. An unattended beat passes None and the field stays absent, so the run
    log never attributes a spent window to someone who was not there."""
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

        if due_for_lint:
            kind, batch, objects, report = "lint", [], None, _lint_report(substrate, program)
        else:
            kind, report = "ingest", ""
            chosen = pending[:wiki_batch()]
            objects = [(o, wiki_store.object_hash(o)) for o in chosen]
            batch = [o.oid for o in chosen]

        # Snapshotted here, AFTER _lint_report ran (which now writes autofixed
        # pages to the bundle via run_lint(fix=True)), not before it. dirty_before
        # is the collect half's "state of the world before the agent started";
        # taking it earlier would predate autofix's own writes and attribute them
        # to the agent's run instead of to the fix step that produced them.
        dirty_before = _dirty_paths(substrate)

        token = agent.launch(kind=kind, program=program, bundle=bundle,
                             run_dir=run_dir, objects=objects, report=report,
                             model=program.wiki_model)
        # ingests_since_lint is NOT reset here: it resets when a lint run collects
        # ok, so a lint run that fails is still owed.
        state["run"] = {"id": run_id, "kind": kind, "batch": batch, "token": token,
                        "started_at": now, "model": program.wiki_model,
                        "dirty_before": dirty_before}
        if forced_by:
            state["run"]["forced_by"] = forced_by
        return f"wiki: launched {kind} {run_id}" + (
            f" ({len(batch)} object{'s' if len(batch) != 1 else ''})" if batch else "")


def _lint_report(substrate, program) -> str:
    """The machine report a lint run is handed. The deterministic fixes are
    applied here, before the agent starts, so its turn goes on judgement calls."""
    from coscience import wiki_lint
    findings, _fixed = wiki_lint.run_lint(substrate, program.id, fix=True)
    return wiki_lint.render_report(findings)


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


def _count_failure(state: dict, batch: list[str]) -> bool:
    """Count one failed or escaped run against the shared threshold. True when
    that pushed the batch into quarantine.

    One counter covers both kinds deliberately: the threshold exists so that a
    batch which keeps going wrong stops being retried, and it is equally wrong
    whether the agent exited non-zero or wrote somewhere it should not have."""
    state["failures"] = state.get("failures", 0) + 1
    if state["failures"] < max_failures():
        return False
    state["failures"] = 0
    if not batch:
        return False
    quarantined = list(state.get("quarantined") or [])
    quarantined += [oid for oid in batch if oid not in quarantined]
    state["quarantined"] = quarantined
    return True


def _reconciled(batch: list[str], report: dict) -> list[str]:
    """The objects a run is allowed to mark ingested: dispatched AND reported as
    covered.

    `batch` is what the platform handed out; the prompt tells the agent to do
    fewer objects well and say which in `report.json`. Trusting `batch` alone
    marks the honestly-skipped ones done, and they never come back through
    pending_objects() — a silent, permanent hole. An absent or malformed
    `objects` field falls back to the whole batch, because spec §8.6 keeps a run
    with no report at all an `ok` run with unknown counts."""
    claimed = report.get("objects")
    if not isinstance(claimed, list):
        return list(batch)
    # Non-string entries are tolerated, not raised on; an oid the agent invented
    # is ignored, since it does not get to widen its own mandate.
    covered = {o for o in claimed if isinstance(o, str)}
    return [oid for oid in batch if oid in covered]


def _pair(winner: str, loser: str) -> list[str]:
    return sorted([winner, loser])


def _proposals(report: dict) -> list[tuple[str, str, str]]:
    """(winner, loser, why) from a report, tolerating anything. An exit-0 run is
    done; a malformed report is a bad page, never a crashed beat."""
    out = []
    for entry in (report.get("merges") or []):
        if not isinstance(entry, dict):
            continue
        winner, loser = str(entry.get("winner") or ""), str(entry.get("loser") or "")
        if winner and loser:
            out.append((winner, loser, str(entry.get("why") or "")))
    return out


def _next_merge_id(state: dict) -> str:
    """Monotonic high-water mark, not max() over the CURRENTLY PENDING queue
    (contrast _next_run_id, which is high-water for exactly this reason): a
    proposal's id must never be reused once it leaves the queue, or a stale
    browser tab still showing the old card can Accept a different pair a
    later run happened to queue under the same id — on a destructive
    operation (spec 9.1)."""
    n = int(state.get("merge_seq") or 0)
    for p in (state.get("merge_proposals") or []):
        try:
            n = max(n, int(str(p.get("id", "m0")).lstrip("m")))
        except ValueError:
            pass
    n += 1
    state["merge_seq"] = n
    return f"m{n:04d}"


def _names_a_source(substrate, program_id: str, winner: str, loser: str) -> bool:
    """True if either page is a Source page. Spec 9.1: never merged, under
    EITHER policy — a Source page stands for one real object and is bound to
    it by `resource`/`origin_hash`; merging two would make `src/hash-drift`
    and `src/missing` meaningless. Reads the bundle directly (cheap: two
    files) rather than reaching for new IO machinery; never opens a state
    guard — this runs inside `beat`'s own flock.

    Normalises the same way `merge_wiki_pages` does: an agent's proposal can
    spell a path with or without `.md` (`_proposals` tolerates either), and
    `wiki_store.read_page` needs the literal bundle-relative filename. Reading
    the raw path only closed this for the `.md` spelling — exactly the sloppy
    one `_proposals` was written to tolerate stayed open under `propose`."""
    from coscience.service import _strip_md
    for path in (winner, loser):
        page = wiki_store.read_page(substrate, program_id, f"{_strip_md(path)}.md")
        if page is not None and page.type == "Source":
            return True
    return False


def _handle_merges(substrate, program, state, report, run_id, now) -> list[dict]:
    """Apply or queue what the agent proposed. Returns the pairs actually merged,
    each as {"loser", "winner", "commit"} — the commit is the undo (spec 9.1).

    Which of the two happens is the ONLY difference between the policies —
    the agent's instructions and prohibitions are identical either way (spec 9.1).
    That includes refusals: a pair naming a Source page is checked and refused
    BEFORE the policy split, so `propose` never queues a choice that can never
    be accepted.

    Skips a pair already in `merges_refused` (a human said no, or the platform
    already refused it) AND a pair already sitting in `merge_proposals`
    (already queued) — without the second check, every lint run in `propose`
    mode would append a fresh duplicate proposal for the same two pages."""
    from coscience.service import NotFoundError, Service
    refused = {tuple(sorted(p)) for p in (state.get("merges_refused") or [])
               if isinstance(p, list) and len(p) == 2}
    queued = {tuple(sorted([p.get("winner", ""), p.get("loser", "")]))
              for p in (state.get("merge_proposals") or []) if isinstance(p, dict)}
    merged: list[dict] = []
    service = None
    for winner, loser, why in _proposals(report):
        pair = _pair(winner, loser)
        if tuple(pair) in refused or tuple(pair) in queued:
            continue                       # a human already said no, or it's already queued
        if _names_a_source(substrate, program.id, winner, loser):
            state.setdefault("merges_refused", []).append(pair)
            refused.add(tuple(pair))
            continue
        if getattr(program, "wiki_merge", "auto") != "auto":
            state.setdefault("merge_proposals", []).append(
                {"id": _next_merge_id(state), "winner": winner, "loser": loser,
                 "why": why, "run": run_id, "at": now})
            queued.add(tuple(pair))        # do not queue the same pair twice
            continue
        service = service or Service(substrate.repo_root)
        try:
            out = service.merge_wiki_pages(program.id, winner, loser)
        except (NotFoundError, ValueError):
            # A judgement that this merge was wrong (a page moved/vanished, or
            # the pair resolves to itself/a Source page) — mirrors
            # accept_wiki_merge's split. Anything else (an OSError, a
            # CalledProcessError from a concurrent git lock) is not a verdict
            # that the merge was wrong, and must not blacklist the pair —
            # propagate and let the beat's own catch-all report it, so the
            # pair is retried instead of quarantined forever.
            state.setdefault("merges_refused", []).append(pair)
            refused.add(tuple(pair))
            continue
        merged.append({"loser": loser, "winner": winner, "commit": out.get("commit", "")})
    return merged


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
    merged: list[dict] = []
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
        # bundle may equally have written the wrong thing inside it. It does count
        # against the failure threshold, though — otherwise a batch that escapes
        # deterministically is relaunched every eligible beat, forever.
        state["last_run"]["status"] = "escaped"
        _count_failure(state, batch)
        state["runs"] = ([{"id": run_id, "kind": kind,
                          "status": state["last_run"]["status"], "at": now,
                          "pages_created": state["last_run"]["pages_created"],
                          "pages_updated": state["last_run"]["pages_updated"],
                          "merged": merged}]
                         + list(state.get("runs") or []))[:RUNS_KEPT]
        substrate.commit(f"wiki {program.id}: {kind} {run_id} wrote outside the bundle")
        return f"wiki: {kind} ESCAPED — batch not recorded"

    line = f"wiki: {kind} {status}"
    if status == "ok":
        objects = {o.oid: o for o in wiki_store.program_objects(substrate, program.id)}
        for oid in _reconciled(batch, report):
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
        merged = _handle_merges(substrate, program, state, report, run_id, now)
    elif _count_failure(state, batch):
        line = f"wiki: {kind} quarantined {len(batch)}"

    state["runs"] = ([{"id": run_id, "kind": kind, "status": state["last_run"]["status"],
                       "at": now,
                       "pages_created": state["last_run"]["pages_created"],
                       "pages_updated": state["last_run"]["pages_updated"],
                       "merged": merged}]
                     + list(state.get("runs") or []))[:RUNS_KEPT]
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


def reconcile(substrate, program_id: str, *, apply: bool = False,
              now: float = 0.0) -> dict:
    """Credit every object the bundle can prove was already ingested.

    `beat`/`_collect` account at RUN granularity while the agent works at OBJECT
    granularity: credit lands only when a run exits ok and leaves a parseable
    report. A run killed partway — a rate limit, a deploy, a kill — has already
    committed its pages, and loses credit for all of them. `_count_failure` then
    reads that as the batch being bad and quarantines it, so an environmental
    outage ends up permanently excluding good content from the wiki.

    The repair reads the provenance a Source page already carries: `origin` names
    the object and `origin_hash` names the bytes it was written from. Equality
    with the live hash is proof of ingest against current content — the same pair
    `src/hash-drift` and `src/missing` already rest on, so this adds no new trust
    assumption. Anything short of that proof credits nothing: a page with no
    `origin_hash`, or one naming a different object, leaves its object pending.

    Returns {"credited", "drift", "already", "pending"} — sorted oid lists.
    Reads only; pass apply=True to write the state. Never touches the bundle:
    drift is REPORTED, never repaired, because only a real run can rewrite a page
    against moved content."""
    pages = {}                             # oid -> declared origin_hash
    for page in wiki_store.iter_pages(substrate, program_id):
        if page.type != "Source":
            continue
        oid = str(page.extra.get("origin") or "")
        if oid:
            pages[oid] = str(page.extra.get("origin_hash") or "")

    state = wiki_store.load_state(substrate, program_id)
    ingested = dict(state.get("ingested") or {})
    credited, drift, already, pending = [], [], [], []

    for obj in wiki_store.program_objects(substrate, program_id):
        current = wiki_store.object_hash(obj)
        if not current:
            continue                       # the bytes are gone; lint reports src/missing
        if (ingested.get(obj.oid) or {}).get("hash", "") == current:
            already.append(obj.oid)
            continue
        declared = pages.get(obj.oid, "")
        if not declared:
            # No page, or a page that never recorded which bytes it read. Either
            # way the bundle proves nothing and the object is owed a real run.
            pending.append(obj.oid)
        elif declared == current:
            credited.append(obj.oid)
        else:
            drift.append(obj.oid)

    if apply and credited:
        with wiki_store.state_guard(substrate, program_id) as live:
            for oid in credited:
                obj = next((o for o in wiki_store.program_objects(substrate, program_id)
                            if o.oid == oid), None)
                live.setdefault("ingested", {})[oid] = {
                    "hash": wiki_store.object_hash(obj) if obj else "",
                    "at": now, "run": "reconcile"}
            # Quarantine is a content verdict; these objects just proved their
            # content is fine, so the verdict does not survive the evidence.
            freed = set(credited)
            live["quarantined"] = [q for q in (live.get("quarantined") or [])
                                   if q not in freed]

    return {"credited": sorted(credited), "drift": sorted(drift),
            "already": sorted(already), "pending": sorted(pending)}
