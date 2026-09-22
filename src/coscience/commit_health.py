"""Whether the substrate is actually accepting commits (B4).

On 2026-09-20 the substrate's git repo refused every commit for twelve hours and nothing
anywhere said so. `Substrate.commit` ran `add` with `check=True` but the `commit` after
it with `check=False`, so a commit that failed returned "" — the same value it returns
when there was simply nothing to commit. Every caller read that as "nothing to commit"
and carried on. The platform kept working and the files were all on disk; what was lost
was the history, and the ability to notice.

The state lives in this host's cache, not in the substrate: a repo that cannot commit
cannot be asked to record the fact that it cannot commit, and a file written inside it
would be swept into the next `git add -A` as content.

One failure is not news — a commit can lose a race with another writer and the next beat
will carry the same content. What matters is a repo that has stopped accepting work, so
the record keeps `since` (when the current run of failures began) and `count`, and a
single success clears it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from coscience.usage_meter import cache_root, substrate_key

# Below this many consecutive failures the dashboard stays quiet: a lost race or a
# momentary lock is not an outage, and the next beat usually settles it.
REPORT_AFTER = 3


def _path(repo_root) -> Path:
    return cache_root() / "commit-health" / f"{substrate_key(repo_root)}.json"


def read(repo_root) -> dict | None:
    """{since, count, reason} while commits are failing, else None. An unreadable or
    malformed file reads as None: this is a warning channel, and a broken warning must
    not become an error of its own."""
    try:
        d = json.loads(_path(repo_root).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("count"):
        return None
    return {"since": float(d.get("since") or 0.0),
            "count": int(d.get("count") or 0),
            "reason": str(d.get("reason") or "")}


def record_failure(repo_root, reason: str, now: float | None = None) -> dict:
    """Note that a commit failed, keeping the start of the current run."""
    now = time.time() if now is None else now
    prev = read(repo_root) or {}
    entry = {"since": prev.get("since") or now,
             "count": int(prev.get("count") or 0) + 1,
             "reason": " ".join((reason or "").split())[:500]}
    _write(repo_root, entry)
    return entry


def record_success(repo_root) -> None:
    """A commit landed: whatever was wrong is over."""
    if read(repo_root) is None:
        return                      # already clean; don't write on every commit
    _write(repo_root, {})


def _write(repo_root, entry: dict) -> None:
    path = _path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=2))
        tmp.replace(path)
    except OSError:
        pass                        # a full disk is one cause of this; never raise here


def describe(entry: dict | None) -> str:
    """One line for a human, or "" when there is nothing to say. Silent until the
    failures have repeated, so a single lost race never reaches the dashboard."""
    if not entry or entry["count"] < REPORT_AFTER:
        return ""
    mins = max(1, int((time.time() - entry["since"]) / 60))
    span = f"{mins} min" if mins < 90 else f"{mins // 60} h"
    return (f"the substrate has not committed for {span} "
            f"({entry['count']} attempts) — {entry['reason']}")
