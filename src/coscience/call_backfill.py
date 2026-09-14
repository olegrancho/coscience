"""Backfill the call log from run history already on disk (todo F5).

The call log is an index over the run directories, not the system of record, so
the history from before every call site recorded itself is recoverable: each wiki
run dir holds the final `result` envelope of its stream, and each sprint dir holds
the cost sidecar of its most recent worker run. This rebuilds a start and an end
event for every such run the log does not already have.

A run counts as already logged when the log holds a call of the same kind for the
same program (wiki) or sprint (worker) that ended within two minutes of it, or a
row this backfill wrote before — so running it twice adds nothing. Backfilled rows
carry `backfilled: true`. A worker sidecar does not say how its run ended, so those
rows have status `unknown` rather than a guessed `ok`.

Dry run by default:  python -m coscience.call_backfill
Write the rows:      python -m coscience.call_backfill --apply"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from coscience import usage_meter

WINDOW = 120.0   # seconds either side of an existing call's end that count as the same run


def _last_result(path: Path) -> dict | None:
    try:
        raw = path.read_text()
    except OSError:
        return None
    envelope = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            envelope = ev
    return envelope


def _rid(source: str) -> str:
    return "backfill-" + hashlib.sha1(source.encode()).hexdigest()[:12]


def wiki_runs(repo_root) -> list[dict]:
    """One candidate row per wiki run dir that left a result envelope."""
    from coscience import wiki_agent
    repo_root = Path(repo_root)
    out = []
    for run_dir in sorted(repo_root.glob("programs/*/.wiki/runs/r*")):
        stream = run_dir / "agent.out"
        envelope = _last_result(stream)
        if envelope is None:
            continue
        outcome = wiki_agent.read_outcome(run_dir)
        status = outcome.pop("status", "ok")
        marker = run_dir / "agent.exit"
        end = (marker if marker.exists() else stream).stat().st_mtime
        try:
            head = (run_dir / "instructions.md").read_text().splitlines()[0].lower()
        except (OSError, IndexError):
            head = ""
        kind = "wiki-lint" if "lint" in head else "wiki-ingest"
        duration = float(envelope.get("duration_ms") or 0) / 1000.0
        source = str(run_dir.relative_to(repo_root))
        out.append({"rid": _rid(source), "source": source, "kind": kind,
                    "program": run_dir.parents[2].name, "sprint": "",
                    "start": end - duration, "end": end, "status": status, **outcome})
    return out


def worker_runs(repo_root) -> list[dict]:
    """One candidate row per sprint cost sidecar — its most recent worker run."""
    from coscience.substrate import Substrate
    repo_root = Path(repo_root)
    substrate = Substrate(repo_root)
    out = []
    for sidecar in sorted(repo_root.glob("sprints/*/agent.cost.json")):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        sprint_id = sidecar.parent.name
        try:
            sprint = substrate.load_sprint(sprint_id)
            program, model = sprint.program or "", sprint.model
        except Exception:
            program, model = "", ""
        end = sidecar.stat().st_mtime
        duration = float(data.get("duration_ms") or 0) / 1000.0
        source = str(sidecar.relative_to(repo_root))
        row = {"rid": _rid(f"{source}@{end:.0f}"), "source": source, "kind": "worker",
               "program": program, "sprint": sprint_id, "start": end - duration, "end": end,
               "status": "unknown", "model": model}
        for key in ("cost", "tokens", "usage", "turns", "limits", "limits_before"):
            if data.get(key) is not None:
                row[key] = data[key]
        out.append(row)
    return out


def _already_logged(row: dict, calls: list[dict], rids: set[str]) -> bool:
    if row["rid"] in rids:
        return True
    for c in calls:
        ended = c.get("ended_at")
        if ended is None or abs(float(ended) - row["end"]) > WINDOW:
            continue
        if row["kind"] == "worker":
            if c.get("kind") == "worker" and c.get("sprint") == row["sprint"]:
                return True
        elif str(c.get("kind") or "").startswith("wiki") and c.get("program") == row["program"]:
            return True
    return False


def backfill(repo_root, *, apply: bool = False) -> dict:
    """{"added": [rows], "skipped": n}. Writes the added rows only with apply=True."""
    rids = {str(r.get("rid") or "") for r in usage_meter.load_runs(repo_root)}
    calls = usage_meter.calls(repo_root)
    added, skipped = [], 0
    for row in wiki_runs(repo_root) + worker_runs(repo_root):
        if _already_logged(row, calls, rids):
            skipped += 1
            continue
        added.append(row)
        rids.add(row["rid"])
        if apply:
            _write(repo_root, row)
    return {"added": added, "skipped": skipped}


def _write(repo_root, row: dict) -> None:
    start = {"ev": "start", "rid": row["rid"], "ts": row["start"], "kind": row["kind"]}
    for key in ("program", "sprint", "model"):
        if row.get(key):
            start[key] = row[key]
    end = {"ev": "end", "rid": row["rid"], "ts": row["end"], "status": row["status"],
           "backfilled": True, "source": row["source"]}
    for key in ("cost", "tokens", "turns", "model", "limits", "limits_before"):
        if row.get(key) is not None:
            end[key] = row[key]
    if isinstance(row.get("usage"), dict):
        end.update(row["usage"])
    usage_meter._append(repo_root, start)
    usage_meter._append(repo_root, end)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m coscience.call_backfill",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.environ.get("COSCIENCE_REPO", "."))
    ap.add_argument("--apply", action="store_true", help="write the rows (default: dry run)")
    args = ap.parse_args(argv)
    result = backfill(args.repo, apply=args.apply)
    cost = sum(float(r.get("cost") or 0) for r in result["added"])
    for r in result["added"]:
        print(f"  + {r['kind']:12} {r['sprint'] or r['program']:44} "
              f"${float(r.get('cost') or 0):6.2f}  {r['status']:12} {r['source']}")
    verb = "added" if args.apply else "would add"
    print(f"{verb} {len(result['added'])} calls (${cost:.2f}); "
          f"{result['skipped']} already in the log", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
