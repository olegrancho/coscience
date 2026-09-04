"""Claude usage visibility for the dashboard.

Two things the loops can't surface from their own memory (they're separate
processes from the HTTP server): how much Claude work each role has done, and how
much of the rolling budget is left.

- The PM and worker append one line to `.coscience/runs.jsonl` per Claude call
  (a PM reasoner cycle, a worker agent launch). The server aggregates them.
- The 5h / weekly budget comes from the rate-limit reading Claude Code puts in
  every run's own event stream, recorded by whoever parses that stream. The usage
  skill is the fallback for a host that hasn't run Claude lately, called rarely
  because its endpoint rate-limits callers.
"""
from __future__ import annotations

import datetime
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
_LIMITS_MAX_AGE = 900.0   # a reading older than this describes a window that may have reset
_HOUR = 3600
_DAY = 86400

_output_cache: dict = {"ts": 0.0, "out": None}


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


def _stale_reading(out: str, now: float) -> bool:
    """True if the usage skill served a cache old enough that its percentages
    describe a window which has since reset. Reporting one of those is worse than
    reporting nothing: the dashboard showed a frozen 100% for eight hours from a
    reading taken eleven minutes before that window rolled over."""
    m = re.search(r"\[cached (\S+)\]", out)
    if not m:
        return False
    try:
        fetched = datetime.datetime.strptime(
            m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return True
    return (now - fetched.timestamp()) > _LIMITS_MAX_AGE


def limits_path() -> Path:
    """Where this host records the last rate-limit reading Claude Code gave it.

    Host-local, not in the substrate: the reading is a property of the account and
    the box, and a file churning inside the substrate would land in every dispatch
    commit."""
    return Path(os.environ.get(
        "COSCIENCE_LIMITS_CACHE",
        os.path.expanduser("~/.cache/coscience/rate-limit.json")))


def record_limits(info: dict | None) -> None:
    """Store one `rate_limit_info` from a Claude stream. No-op on None, so a caller
    can pass `agent_stream.parse_rate_limit(raw)` straight through. Best-effort and
    atomic — readers poll this file and must never catch it half written."""
    if not isinstance(info, dict) or not info:
        return
    try:
        path = limits_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "info": info}))
        tmp.replace(path)
    except OSError:
        pass


def _window(w) -> dict | None:
    """One `unifiedWindows` entry as {pct, resets}. `utilization` there is a
    FRACTION (0.57 == 57%), unlike the usage API's 0-100 — the two feed the same
    dashboard field, so the conversion belongs in one place."""
    if not isinstance(w, dict) or w.get("utilization") is None:
        return None
    try:
        pct = round(float(w["utilization"]) * 100)
    except (TypeError, ValueError):
        return None
    resets = "?"
    if w.get("resetsAt") is not None:
        try:
            dt = (datetime.datetime.fromtimestamp(int(w["resetsAt"]),
                                                  datetime.timezone.utc)
                  .astimezone())
            resets = f"{dt.strftime('%a')} {dt.hour}:{dt.strftime('%M')}"
        except (TypeError, ValueError, OSError):
            resets = "?"
    return {"pct": pct, "resets": resets}


def read_limits(max_age: float = _LIMITS_MAX_AGE, now: float | None = None) -> dict | None:
    """The last recorded rate-limit reading as {windows: {"5h"/"week": {pct, resets}},
    status, live: True}, or None when there is none or it is older than `max_age`
    (a stale reading describes a window that may have reset since).

    `windows` is empty when the payload carried no `unifiedWindows` — older Claude
    Code sends `status` alone. `status` is the verdict on the request that produced
    the reading, not a percentage: anything other than "allowed" means that call was
    throttled."""
    now = time.time() if now is None else now
    try:
        rec = json.loads(limits_path().read_text())
        ts = float(rec["ts"])
        info = rec["info"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(info, dict) or now - ts > max_age:
        return None
    unified = info.get("unifiedWindows")
    windows = {}
    if isinstance(unified, dict):
        for key, src in (("5h", "five_hour"), ("week", "seven_day")):
            got = _window(unified.get(src))
            if got:
                windows[key] = got
    return {"windows": windows, "status": str(info.get("status") or ""),
            "live": True}


def usage_output(ttl: float = 300.0, now: float | None = None) -> str | None:
    """The usage skill's line, at most one call per `ttl` seconds in this process.
    None when the script can't be run at all.

    Throttled because the endpoint behind it rate-limits callers, and every gate
    check used to run it: a 5-second dispatch loop asking twice a cycle got the host
    429'd for hours, after which the script served an 8-hour-old cache as if it were
    current."""
    now = time.time() if now is None else now
    if _output_cache["out"] is not None and now - _output_cache["ts"] < ttl:
        return _output_cache["out"]
    try:
        out = subprocess.run([sys.executable, usage_script_path()],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    _output_cache.update(ts=now, out=out)
    return out


def read_budget(ttl: float = 300.0) -> dict | None:
    """The rolling 5h / weekly Claude budget, as {windows: {label: {pct, resets}},
    live: bool}, or None when no current reading can be had.

    Prefers what Claude Code already told us (`read_limits`) — free, and refreshed by
    every run this host makes. An idle host with no recent run falls back to the
    usage skill via `usage_output`. None rather than a stale number: a percentage
    from a window that has since reset is worse than an empty panel."""
    recorded = read_limits()
    if recorded and recorded["windows"]:
        return {"windows": recorded["windows"], "live": True}
    now = time.time()
    out = usage_output(ttl, now=now)
    if out is None or _stale_reading(out, now):
        return None
    windows: dict[str, dict] = {}
    for label, pct, reset in _USAGE_RE.findall(out):
        key = "week" if label.lower().startswith("week") else label.lower()
        windows[key] = {"pct": int(pct), "resets": reset.strip()}
    if not windows:
        return None
    return {"windows": windows, "live": "[live]" in out}
