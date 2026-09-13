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
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
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


def cache_root() -> Path:
    """This host's cache directory. One env override so tests (and a box with an
    unusual HOME) can move it without every caller learning the path."""
    return Path(os.environ.get("COSCIENCE_CACHE_DIR",
                               os.path.expanduser("~/.cache/coscience")))


def _substrate_key(repo_root) -> str:
    """A stable per-substrate filename. The basename alone will not do: both real
    substrates are directories named `coscience`, so keying on it would merge one
    program's spend into another's. The hash disambiguates, the name keeps the
    file identifiable by eye."""
    resolved = str(Path(repo_root).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()[:12]
    return f"{Path(resolved).name}-{digest}"


def runs_path(repo_root) -> Path:
    """Where this host records Claude calls for one substrate.

    Host-local, NOT in the substrate: the old location was git-tracked, so every
    dispatch commit churned it (121 commits for 234 rows) and the rate would only
    rise as more call sites started recording. Safe to keep out of git because the
    run directories stay in the substrate — the log is an index over them, not the
    system of record, and can be rebuilt from the agent envelopes on disk."""
    return cache_root() / "runs" / f"{_substrate_key(repo_root)}.jsonl"


def legacy_runs_path(repo_root) -> Path:
    """The pre-2026-09 location, inside the substrate and git-tracked.

    Read but never written. Treating it as a frozen archive rather than something
    to migrate means nothing is moved and nothing can be lost in the moving; a
    `git rm --cached` becomes an independent tidy-up rather than a step that has
    to happen in the right order."""
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
        _append(repo_root, rec)
    except OSError:
        pass


CALL_GRACE = 900.0      # a start older than this with no end is a dead process


def _append(repo_root, rec: dict) -> None:
    """One line, one write. Small records in O_APPEND are atomic on Linux, so
    concurrent loops interleave rows rather than corrupting them — and `load_runs`
    already drops a line it cannot parse, so even a torn write costs one row
    instead of the log."""
    path = runs_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def start_call(repo_root, kind: str, *, program: str = "", sprint: str = "",
               model: str = "", limits=None, token: str = "",
               now: float | None = None) -> str:
    """Record that a Claude call is beginning; returns the id to finish it with.

    The launch half exists so a call that never comes back still leaves a trace.
    An end-only row cannot describe a killed process, and killed processes are
    most of what the log is for. Best-effort: an id is returned even if the write
    failed, so a caller never has to handle logging errors.

    `token` is the '<pid>:<starttime>' of the process doing the call, so `calls()`
    can tell a long run from a dead one."""
    rid = uuid.uuid4().hex[:16]
    try:
        rec = {"ev": "start", "rid": rid, "ts": time.time() if now is None else now,
               "kind": kind}
        for key, val in (("program", program), ("sprint", sprint), ("model", model),
                         ("token", token)):
            if val:
                rec[key] = val
        if limits:
            rec["limits"] = limits
        _append(repo_root, rec)
    except OSError:
        pass
    return rid


def finish_call(repo_root, rid: str, *, status: str = "ok", cost=None, tokens=None,
                usage=None, turns=None, prompt_bytes=None, model: str = "",
                limits=None, limits_before=None, now: float | None = None) -> None:
    """Close the call `rid` opened. `status` is one of ok / failed / rate-limited /
    escaped — `lost` and `running` are never written, only inferred on read.

    `limits` is where the budget ended up; `limits_before` is where it stood when
    the run began, read from the run's own stream. The launch stamp cannot be
    trusted to land — it needs a live OAuth token the box lacks after an idle
    stretch — so the authoritative `before` arrives here, at the end."""
    try:
        rec = {"ev": "end", "rid": rid, "ts": time.time() if now is None else now,
               "status": status}
        if cost is not None:
            rec["cost"] = float(cost)
        if tokens is not None:
            rec["tokens"] = int(tokens)
        if usage:
            rec.update(usage)
        if turns is not None:
            rec["turns"] = int(turns)
        if prompt_bytes is not None:
            rec["prompt_bytes"] = int(prompt_bytes)
        if model:
            rec["model"] = model
        if limits:
            rec["limits"] = limits
        if limits_before:
            rec["limits_before"] = limits_before
        _append(repo_root, rec)
    except OSError:
        pass


def load_runs(repo_root) -> list[dict]:
    """Every event for this substrate, archive first then live. A line that will
    not parse is dropped rather than raised on — one torn row must not cost the
    whole log."""
    out: list[dict] = []
    for path in (legacy_runs_path(repo_root), runs_path(repo_root)):
        try:
            if not path.is_file():
                continue
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


# A legacy row carries `ref`, which meant program for a PM call and sprint for a
# worker call. Nothing else can be recovered, so the mapping is by kind.
_REF_IS_SPRINT = {"worker"}


def _legacy_call(rec: dict) -> dict:
    """A pre-split row: written at the END of a call, so complete on its own."""
    ts = float(rec.get("ts") or 0.0)
    ref = str(rec.get("ref") or "")
    kind = str(rec.get("kind") or "")
    call = {k: v for k, v in rec.items()
            if k not in ("ts", "ref", "kind", "ok", "ev", "rid", "limits")}
    call.update({
        "id": f"legacy:{kind}:{ts}", "kind": kind,
        "program": "" if kind in _REF_IS_SPRINT else ref,
        "sprint": ref if kind in _REF_IS_SPRINT else "",
        "started_at": None, "ended_at": ts, "duration": None,
        "status": "failed" if rec.get("ok") is False else "ok",
        "limits_before": None, "limits_after": None,
    })
    call.setdefault("cost", None)
    call.setdefault("model", "")
    return call


def calls(repo_root, now: float | None = None,
          grace: float = CALL_GRACE) -> list[dict]:
    """Every Claude call as one row, newest first — what the Compute log renders.

    Two events fold into one row. A start with no end is `running` while the
    process named by its token is alive, whatever its age — a worker run of an
    hour is normal, and age alone had shown healthy ones as `lost` — and `lost`
    as soon as that process is gone. Every site closes its call within a
    dispatcher cycle of the process exiting (chat turns included, since the
    dispatcher collects them), so a run that finished normally can read `lost`
    only for that moment. A start with no process token (older rows, test fakes)
    keeps the age rule: `running` inside `grace`, `lost` beyond it. Either verdict
    is INFERRED here rather than written by anyone, because the process that
    would have written it is the one that died. A 429 is not that case — the
    agent exits with a full envelope, so those arrive as `rate-limited` with
    their cost intact."""
    now = time.time() if now is None else now
    folded: dict[str, dict] = {}
    order: list[str] = []
    out: list[dict] = []
    tokens: dict[str, str] = {}

    for rec in load_runs(repo_root):
        ev = rec.get("ev")
        if ev not in ("start", "end"):
            out.append(_legacy_call(rec))
            continue
        rid = str(rec.get("rid") or "")
        if not rid:
            continue
        call = folded.get(rid)
        if call is None:
            call = folded[rid] = {"id": rid, "kind": "", "program": "", "sprint": "",
                                  "model": "", "cost": None, "started_at": None,
                                  "ended_at": None, "duration": None, "status": "",
                                  "limits_before": None, "limits_after": None}
            order.append(rid)
        ts = float(rec.get("ts") or 0.0)
        if ev == "start":
            call["started_at"] = ts
            call["limits_before"] = rec.get("limits")
            if rec.get("token"):
                tokens[rid] = str(rec["token"])
            for k in ("kind", "program", "sprint", "model"):
                if rec.get(k):
                    call[k] = rec[k]
        else:
            call["ended_at"] = ts
            call["limits_after"] = rec.get("limits")
            # The run's own opening reading, when it left one, outranks the launch
            # stamp: it is Claude's own number and cannot be a stale cache. Absent,
            # whatever the launch managed to record stands.
            if rec.get("limits_before"):
                call["limits_before"] = rec["limits_before"]
            call["status"] = str(rec.get("status") or "ok")
            for k, v in rec.items():
                if k not in ("ev", "rid", "ts", "status", "limits", "limits_before"):
                    call[k] = v

    for rid in order:
        call = folded[rid]
        if call["started_at"] is not None and call["ended_at"] is not None:
            call["duration"] = round(call["ended_at"] - call["started_at"], 3)
        elif not call["status"]:
            # Never written, always derived — see the docstring.
            token = tokens.get(rid, "")
            if _is_pid_token(token):
                call["status"] = "running" if _process_alive(token) else "lost"
            else:
                age = now - (call["started_at"] or 0.0)
                call["status"] = "lost" if age > grace else "running"
        out.append(call)

    out.sort(key=lambda c: (c.get("ended_at") or c.get("started_at") or 0.0),
             reverse=True)
    return out


def _is_pid_token(token: str) -> bool:
    """A '<pid>:<starttime>' token this host can check, as opposed to none or a fake."""
    return bool(token) and token.partition(":")[0].isdigit()


def _process_alive(token: str) -> bool:
    """Whether the '<pid>:<starttime>' process is still this host's live process.
    False for no token or one that is not a pid token (test fakes), which leaves
    the call to the age rule."""
    if not token:
        return False
    from coscience.executor import is_running
    try:
        return is_running(token)
    except (ValueError, OverflowError, OSError):
        return False


def run_stats(repo_root, now: float | None = None) -> dict:
    """Per-kind totals for the Compute tiles, computed over folded CALLS rather
    than raw events — a start and its end are one call, not two, and the cost
    only lands on the end.

    `pm` and `worker` are always present so the panel keeps its shape on an empty
    substrate; any other kind appears once it has a call."""
    now = time.time() if now is None else now
    rows = calls(repo_root, now=now)
    kinds = {"pm", "worker"} | {str(c.get("kind") or "") for c in rows}

    def agg(kind: str) -> dict:
        rs = [c for c in rows if c.get("kind") == kind]
        ts = [float(c.get("ended_at") or c.get("started_at") or 0.0) for c in rs]
        return {
            "total": len(rs),
            "last_hour": sum(1 for t in ts if now - t <= _HOUR),
            "last_day": sum(1 for t in ts if now - t <= _DAY),
            "last": max(ts) if ts else None,
            "cost": round(sum(float(c.get("cost") or 0) for c in rs), 4),
            "cost_day": round(sum(float(c.get("cost") or 0) for c, t in zip(rs, ts)
                                  if now - t <= _DAY), 4),
            "tokens": sum(int(c.get("tokens") or 0) for c in rs),
            # Rows written before the per-component split contribute 0 here while
            # still counting in `tokens`, so a partial split is expected on a
            # substrate with history.
            **{k: sum(int(c.get(k) or 0) for c in rs)
               for k in (*TOKEN_FIELDS, "thinking_tokens")},
            "failed": sum(1 for c in rs
                          if c.get("status") in ("failed", "rate-limited",
                                                 "escaped", "lost")),
        }

    return {kind: agg(kind) for kind in sorted(kinds) if kind}


def recent_calls(repo_root, limit: int = 200, now: float | None = None) -> list[dict]:
    """The newest `limit` calls, for the Compute log. `calls()` already sorts
    newest-first; this is the bounded read the HTTP layer serves so a substrate
    with years of history cannot render an unbounded table."""
    return calls(repo_root, now=now)[:max(0, int(limit))]


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
    """One `unifiedWindows` entry as {pct, resets, resets_at?}. `utilization` there
    is a FRACTION (0.57 == 57%), unlike the usage API's 0-100 — the two feed the
    same dashboard field, so the conversion belongs in one place. `resets_at` is the
    epoch, kept so the dashboard can place how far into the window we are."""
    if not isinstance(w, dict) or w.get("utilization") is None:
        return None
    try:
        pct = round(float(w["utilization"]) * 100)
    except (TypeError, ValueError):
        return None
    out: dict = {"pct": pct, "resets": "?"}
    if w.get("resetsAt") is not None:
        try:
            at = int(w["resetsAt"])
            dt = datetime.datetime.fromtimestamp(at, datetime.timezone.utc).astimezone()
            out.update(resets=f"{dt.strftime('%a')} {dt.hour}:{dt.strftime('%M')}",
                       resets_at=at)
        except (TypeError, ValueError, OSError):
            pass
    return out


def five_hour_window(info) -> dict | None:
    """The 5h window as {pct, resets} out of a raw `rate_limit_info`, or None.

    Public because a run's own stream is the authoritative "after" reading for
    that call — cheaper and more accurate than asking the usage API once the run
    has already told us."""
    if not isinstance(info, dict):
        return None
    unified = info.get("unifiedWindows")
    if not isinstance(unified, dict):
        return None
    return _window(unified.get("five_hour"))


def current_window() -> dict | None:
    """The 5h reading to stamp on a call as it opens.

    Goes through `read_budget`, not `read_limits`. The latter returns None past
    `_LIMITS_MAX_AGE` — correct for the budget panel, where a percentage from a
    window that may have reset is worse than a blank — but a launch stamp then
    never lands at all, because a launch happens long after the previous call
    refreshed the cache. In production that left `limits_before` empty on six of
    seven wiki calls. `read_budget` prefers the recorded reading and falls back
    to the usage script under its own 300s throttle, so this stays cheap."""
    return ((read_budget() or {}).get("windows") or {}).get("5h")


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
    epochs = _script_reset_epochs()
    for label, pct, reset in _USAGE_RE.findall(out):
        key = "week" if label.lower().startswith("week") else label.lower()
        windows[key] = {"pct": int(pct), "resets": reset.strip()}
        if key in epochs:
            windows[key]["resets_at"] = epochs[key]
    if not windows:
        return None
    return {"windows": windows, "live": "[live]" in out}


def usage_script_cache_path() -> Path:
    """Where the usage skill caches the API payload it printed from."""
    override = os.environ.get("COSCIENCE_USAGE_SCRIPT_CACHE")
    return Path(override) if override else (
        Path.home() / ".claude" / "statusline-usage-cache.json")


def _script_reset_epochs() -> dict[str, int]:
    """{"5h"/"week": reset epoch} from the usage skill's cache. Its printed line
    carries only "Sun 1:50", which the dashboard cannot place in time; the payload
    it cached alongside has the ISO timestamp. Empty when unreadable."""
    try:
        data = json.loads(usage_script_cache_path().read_text())["data"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}
    out = {}
    for key, src in (("5h", "five_hour"), ("week", "seven_day")):
        try:
            iso = (data.get(src) or {})["resets_at"]
            out[key] = int(datetime.datetime.fromisoformat(iso).timestamp())
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out
