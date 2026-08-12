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
import sys
import time
from pathlib import Path

def usage_script_path() -> str:
    """Where the usage skill lives. Overridable because it is a personal dotfile:
    a host without it silently loses BOTH the budget panel and the usage gate, and
    `CLAUDE.md` says more than one host may run the full platform. Resolved per call,
    not at import, so tests and a relaunched process pick up the environment."""
    return os.environ.get("COSCIENCE_USAGE_SCRIPT",
                          os.path.expanduser("~/.claude/skills/usage/usage.py"))


_USAGE_RE = re.compile(r"(\w+):\s*(\d+)%\s*\(resets ([^)]+)\)")
_HOUR = 3600
_DAY = 86400

_budget_cache: dict = {"ts": 0.0, "data": None}


def _runs_path(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "runs.jsonl"


# The four components of a Claude call's token usage, as the API names them.
# Kept verbatim so a row in runs.jsonl reads the same as the upstream envelope.
TOKEN_FIELDS = ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens")


def token_breakdown(usage) -> dict:
    """Split a Claude envelope's `usage` into its components, plus the thinking
    subtotal and their sum.

    One place, because the sum on its own is misleading about cost: a cache read
    bills at a tenth of fresh input and an output token at five times it, so two
    runs reporting the same total can differ several-fold in what they actually
    cost. Absent fields are omitted rather than zero-filled — a row written before
    the split existed must stay distinguishable from a run that genuinely used
    none."""
    if not isinstance(usage, dict):
        return {}
    out = {}
    for k in TOKEN_FIELDS:
        v = usage.get(k)
        if v is not None:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                continue
    # Thinking rides inside output_tokens; broken out because output is the
    # priciest component and thinking is usually most of it.
    details = usage.get("output_tokens_details")
    if isinstance(details, dict) and details.get("thinking_tokens") is not None:
        try:
            out["thinking_tokens"] = int(details["thinking_tokens"])
        except (TypeError, ValueError):
            pass
    if out:
        out["tokens"] = sum(out.get(k, 0) for k in TOKEN_FIELDS)
    return out


def record_run(repo_root, kind: str, ref: str = "", *, cost=None, tokens=None,
               model: str = "", prompt_bytes=None, turns=None, usage=None,
               ok: bool = True) -> None:
    """Append one Claude-call record. `kind` is 'pm' or 'worker'; `ref` is the
    program or sprint id. `cost` (USD), `tokens`, and `model` are recorded when
    known (the agent reports them on a clean run). `prompt_bytes` is the rendered
    prompt we sent — without it the total is unattributable, since a beat's cost is
    roughly the prompt multiplied by however many turns the agent took. `ok=False`
    records a call that raised: it still burned the window, and a call that leaves
    no row makes a retry loop invisible in the ledger. `usage` is a
    `token_breakdown()` result — the per-component split, without which `tokens`
    cannot be read as cost. Best-effort — never let logging break a beat."""
    try:
        rec = {"ts": time.time(), "kind": kind, "ref": ref}
        if cost is not None:
            rec["cost"] = float(cost)
        if tokens is not None:
            rec["tokens"] = int(tokens)
        if usage:
            rec.update(usage)          # carries its own `tokens`, agreeing with the arg
        if model:
            rec["model"] = model
        if prompt_bytes is not None:
            rec["prompt_bytes"] = int(prompt_bytes)
        if turns is not None:
            rec["turns"] = int(turns)
        if not ok:
            rec["ok"] = False          # absent == succeeded, so existing rows still read correctly
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
            # Per-component totals. Rows written before the split existed contribute
            # 0 here while still counting in `tokens`, so a partial split is expected
            # on a substrate with history.
            **{k: sum(int(r.get(k, 0) or 0) for r in rs)
               for k in (*TOKEN_FIELDS, "thinking_tokens")},
            "failed": sum(1 for r in rs if r.get("ok") is False),
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
        out = subprocess.run([sys.executable, usage_script_path()],
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
