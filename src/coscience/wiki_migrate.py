"""Move a program wiki to the next layout (docs/wiki-layouts.md).

A migration is a sequence of ordinary wiki runs — same lock, admission slot, usage
gate, metering and collection as an ingest — driven by the wiki beat:

    setup   copy the bundle's sources, log and questions into a staging bundle
    map     one agent reads the old wiki and writes the topic map
    write   one agent run per batch of 4-5 pages, from the map
    finish  one agent run: syntheses, index, links
    swap    archive the old bundle under .wiki/archive/, move staging into place

The live wiki keeps serving, untouched, until the swap; ingests, lints and sweeps
wait while a migration is under way, so no result lands in a bundle that is about
to be replaced. A phase that fails is retried; after `max_failures()` failures the
migration stops with an error, the old wiki still live, and asking again resumes
the failed phase. Cancelling throws the staging bundle away.

State lives in the program's wiki state under "migration":

    {"to": "topics v1", "from": "concepts v1", "phase": "write", "batch": 3,
     "batches": 9, "failures": 0, "error": "", "requested_by": "...", "at": ...}
"""
from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

from coscience import wiki_layouts, wiki_store, wiki_topics

BATCH = 5


def staging_dir(substrate, program_id: str) -> Path:
    return wiki_store.state_dir(substrate, program_id) / "migration"


def staging_bundle(substrate, program_id: str) -> Path:
    return staging_dir(substrate, program_id) / "bundle"


def map_path(substrate, program_id: str) -> Path:
    # Inside the staging bundle, which is every step's working directory: a wiki
    # run may write only there and in its run directory. Removed at the swap.
    return staging_bundle(substrate, program_id) / ".topic-map.json"


def available(substrate, program_id: str) -> wiki_layouts.Layout | None:
    """The layout a migration of this wiki would produce, or None if it is current
    (or has no pages yet — a new wiki simply starts in the current layout)."""
    bundle = wiki_store.bundle_dir(substrate, program_id)
    if wiki_store.is_empty(substrate, program_id):
        return None
    return wiki_layouts.next_after(wiki_layouts.of(bundle))


def request(substrate, program_id: str, *, by: str, now: float) -> dict:
    """Ask for a migration; the next beat starts it. Asking again after a failure
    resumes the failed phase. Raises ValueError when there is nothing to migrate."""
    with wiki_store.state_guard(substrate, program_id) as state:
        mig = state.get("migration")
        if mig:
            if mig.get("error"):
                mig["error"], mig["failures"] = "", 0
            return dict(mig)
        target = available(substrate, program_id)
        if target is None:
            raise ValueError("this wiki is already in the current layout")
        current = wiki_layouts.of(wiki_store.bundle_dir(substrate, program_id))
        state["migration"] = {"from": str(current), "to": str(target), "phase": "setup",
                              "batch": 0, "batches": 0, "failures": 0, "error": "",
                              "requested_by": by, "at": now}
        return dict(state["migration"])


def cancel(substrate, program_id: str) -> bool:
    """Stop a migration that is not mid-run and discard its staging bundle."""
    with wiki_store.state_guard(substrate, program_id) as state:
        if not state.get("migration"):
            return False
        run = state.get("run") or {}
        if run.get("kind") == "migrate":
            raise ValueError("a migration step is running; cancel once it finishes")
        state.pop("migration", None)
    shutil.rmtree(staging_dir(substrate, program_id), ignore_errors=True)
    return True


def _setup(substrate, program_id: str, target: wiki_layouts.Layout) -> None:
    live = wiki_store.bundle_dir(substrate, program_id)
    stage = staging_bundle(substrate, program_id)
    shutil.rmtree(staging_dir(substrate, program_id), ignore_errors=True)
    for d in wiki_store.PAGE_DIRS:
        (stage / d).mkdir(parents=True, exist_ok=True)
    if (live / "sources").is_dir():
        shutil.rmtree(stage / "sources")
        shutil.copytree(live / "sources", stage / "sources")
    for name in ("log.md", "QUESTIONS.md"):
        if (live / name).is_file():
            shutil.copy2(live / name, stage / name)
    (stage / "CLAUDE.md").write_text(wiki_layouts.schema(target))


def _target_topics(n_pages: int) -> int:
    """Roughly how many topics to ask for: tens, not hundreds (52 old pages -> ~18,
    135 -> ~29 in the two wikis this was calibrated on)."""
    return max(6, min(40, round(2.5 * math.sqrt(max(n_pages, 1)))))


def _batches(topic_map: dict) -> list[list[dict]]:
    out: list[list[dict]] = []
    for key in ("topics", "background"):
        items = list(topic_map.get(key) or [])
        out += [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    return [b for b in out if b]


def _batch_text(batch: list[dict]) -> str:
    lines = []
    for t in batch:
        line = (f"- `{t['slug']}` — **{t.get('title', '')}** "
                f"(aliases: {', '.join(t.get('aliases') or [])}). Scope: {t.get('scope', '')}\n"
                f"  from: {', '.join(t.get('from') or []) or '(no old page: build it from the documentation and the source pages)'}")
        if t.get("docs"):
            line += f"\n  documentation: {', '.join(t['docs'])}"
        lines.append(line)
    return "\n".join(lines)


def instructions(substrate, program, mig: dict) -> str:
    """The instruction document for the migration's current agent phase."""
    pid = program.id
    old = wiki_store.bundle_dir(substrate, pid)
    new = staging_bundle(substrate, pid)
    common = dict(title=program.title, program_id=pid, old=old, new=new,
                  map=map_path(substrate, pid))
    if mig["phase"] == "map":
        n = sum(len(list((old / d).glob("*.md"))) for d in ("concepts", "entities", "syntheses"))
        return wiki_topics.MIGRATE_MAP.format(
            title=program.title, program_id=pid, old=old,
            workdir=program.workdir or "(none recorded)", goals=program.goals,
            schema=wiki_layouts.schema(wiki_layouts.CURRENT), out=map_path(substrate, pid),
            target=_target_topics(n))
    if mig["phase"] == "write":
        batch = _batches(json.loads(map_path(substrate, pid).read_text()))[mig["batch"]]
        return wiki_topics.MIGRATE_WRITE.format(batch=_batch_text(batch), **common)
    if mig["phase"] == "finish":
        return wiki_topics.MIGRATE_FINISH.format(**common)
    raise ValueError(f"no agent step for phase {mig['phase']}")


def next_launch(substrate, program, state: dict) -> tuple[str, Path] | None:
    """Advance any non-agent phase, then return (instructions, cwd) for the next
    agent run, or None when the migration is stopped or just finished."""
    mig = state["migration"]
    if mig.get("error"):
        return None
    if mig["phase"] == "setup":
        _setup(substrate, program.id, _parse(mig["to"]))
        mig["phase"] = "map"
    return instructions(substrate, program, mig), staging_bundle(substrate, program.id)


def _parse(text: str) -> wiki_layouts.Layout:
    name, _, v = text.partition(" v")
    return wiki_layouts.Layout(name, int(v))


def collected(substrate, program, state: dict, status: str, now: float,
              max_failures: int) -> str:
    """Advance after a migration run collects. Returns the beat's summary line."""
    mig = state["migration"]
    phase = mig["phase"]
    if status != "ok":
        if status == "deferred":
            return f"wiki: migrate {phase} deferred — out of budget, will retry"
        mig["failures"] = int(mig.get("failures", 0)) + 1
        if mig["failures"] >= max_failures:
            mig["error"] = f"{phase} failed {mig['failures']} times"
            return f"wiki: migration stopped — {mig['error']}; the old wiki is still live"
        return f"wiki: migrate {phase} failed, will retry"

    mig["failures"] = 0
    if phase == "map":
        try:
            batches = _batches(json.loads(map_path(substrate, program.id).read_text()))
        except (OSError, ValueError) as e:
            mig["error"] = f"the map run wrote no readable topic map ({e})"
            return f"wiki: migration stopped — {mig['error']}"
        mig.update(phase="write", batch=0, batches=len(batches))
        return f"wiki: migrate map ok — {len(batches)} batches to write"
    if phase == "write":
        mig["batch"] += 1
        if mig["batch"] < mig["batches"]:
            return f"wiki: migrate wrote batch {mig['batch']} of {mig['batches']}"
        mig["phase"] = "finish"
        return f"wiki: migrate wrote all {mig['batches']} batches"
    if phase == "finish":
        archived = _swap(substrate, program.id, mig, now)
        state.pop("migration", None)
        # The source pages were copied unchanged and still carry the old wiki's
        # wording; a sweep reads them against the raw results.
        state["sweep_requested"] = True
        state.setdefault("layout_history", []).append(
            {"from": mig["from"], "to": mig["to"], "at": now, "archived": archived})
        return f"wiki: migrated to {mig['to']} — old wiki archived at {archived}"
    return f"wiki: migrate {phase} ok"


def _swap(substrate, program_id: str, mig: dict, now: float) -> str:
    live = wiki_store.bundle_dir(substrate, program_id)
    stage = staging_bundle(substrate, program_id)
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    old = mig["from"].replace(" ", "-")
    archive = wiki_store.state_dir(substrate, program_id) / "archive" / f"{old}-{day}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        shutil.rmtree(archive)
    shutil.move(str(live), str(archive))
    map_path(substrate, program_id).unlink(missing_ok=True)
    shutil.move(str(stage), str(live))
    shutil.rmtree(staging_dir(substrate, program_id), ignore_errors=True)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    log = live / "log.md"
    try:
        text = log.read_text()
    except OSError:
        text = "# Log\n\n"
    entry = (f"- {stamp} — migrated from {mig['from']} to {mig['to']}; the old wiki "
             f"is kept at .wiki/archive/{archive.name}\n")
    lines = text.splitlines(keepends=True)
    at = next((i for i, line in enumerate(lines) if line.startswith("- ")), len(lines))
    log.write_text("".join(lines[:at]) + entry + "".join(lines[at:]))
    return str(archive.relative_to(substrate.program_dir(program_id)))
