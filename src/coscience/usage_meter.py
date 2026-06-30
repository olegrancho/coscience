"""Claude usage visibility for the dashboard.

Two things the loops can't surface from their own memory (they're separate
processes from the HTTP server): how much Claude work each role has done, and how
much of the rolling budget is left.

- The PM and worker append one line to `.coscience/runs.jsonl` per Claude call
  (a PM reasoner cycle, a worker agent launch). The server aggregates them.
- The 5h / weekly budget comes from the usage skill, cached briefly so dashboard
  polling doesn't hammer it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

_USAGE_SCRIPT = os.path.expanduser("~/.claude/skills/usage/usage.py")
_USAGE_RE = re.compile(r"(\w+):\s*(\d+)%\s*\(resets ([^)]+)\)")
_HOUR = 3600
_DAY = 86400

_budget_cache: dict = {"ts": 0.0, "data": None}


def _runs_path(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "runs.jsonl"


def record_run(repo_root, kind: str, ref: str = "", *, cost=None, tokens=None,
               model: str = "") -> None:
    """Append one Claude-call record. `kind` is 'pm' or 'worker'; `ref` is the
    program or sprint id. `cost` (USD), `tokens`, and `model` are recorded when
    known (the agent reports them on a clean run). Best-effort — never let logging
    break a beat."""
    try:
        rec = {"ts": time.time(), "kind": kind, "ref": ref}
        if cost is not None:
            rec["cost"] = float(cost)
        if tokens is not None:
            rec["tokens"] = int(tokens)
        if model:
            rec["model"] = model
        path = _runs_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def load_runs(repo_root) -> list[dict]:
    path = _runs_path(repo_root)
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def run_stats(repo_root, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    runs = load_runs(repo_root)

    def agg(kind: str) -> dict:
        rs = [r for r in runs if r.get("kind") == kind]
        ts = [float(r.get("ts", 0)) for r in rs]
        return {
            "total": len(ts),
            "last_hour": sum(1 for t in ts if now - t <= _HOUR),
            "last_day": sum(1 for t in ts if now - t <= _DAY),
            "last": max(ts) if ts else None,
            "cost": round(sum(float(r.get("cost", 0) or 0) for r in rs), 4),
            "cost_day": round(sum(float(r.get("cost", 0) or 0) for r in rs
                                  if now - float(r.get("ts", 0)) <= _DAY), 4),
            "tokens": sum(int(r.get("tokens", 0) or 0) for r in rs),
        }

    return {"pm": agg("pm"), "worker": agg("worker")}


def read_budget(ttl: float = 60.0) -> dict | None:
    """The rolling 5h / weekly Claude budget, as {windows: {label: {pct, resets}},
    live: bool}. Cached for `ttl` seconds; returns the last value (or None) if the
    usage skill can't be reached."""
    now = time.time()
    cached = _budget_cache["data"]
    if cached is not None and now - _budget_cache["ts"] < ttl:
        return cached
    try:
        out = subprocess.run(["python3", _USAGE_SCRIPT],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return cached
    windows: dict[str, dict] = {}
    for label, pct, reset in _USAGE_RE.findall(out):
        key = "week" if label.lower().startswith("week") else label.lower()
        windows[key] = {"pct": int(pct), "resets": reset.strip()}
    if not windows:
        return cached
    data = {"windows": windows, "live": "[live]" in out}
    _budget_cache.update(ts=now, data=data)
    return data
