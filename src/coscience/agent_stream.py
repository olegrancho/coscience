"""Parse a `claude -p --output-format stream-json` capture.

Pure — no IO. Three callers scan the same JSONL feed for the final `result`
event (the sprint agent, the chat agent, the wiki agent) and each wants a
different thing from it, so this returns the whole record and leaves status
handling to them. The same feed also carries the account's rate-limit standing,
which `parse_rate_limit` picks out."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class StreamResult:
    text: str = ""
    session_id: str = ""
    usage: dict = field(default_factory=dict)
    cost: float | None = None
    turns: int | None = None
    duration_ms: int | None = None


def parse_rate_limit(raw: str) -> dict | None:
    """The `rate_limit_info` of the last `rate_limit_event` in `raw`, or None.

    Claude Code reports the account's usage windows in the stream itself, so a run
    we already capture tells us where the budget stands without asking the usage
    API for it. Emitted about once per session, and older payloads carry only
    `status` and no `unifiedWindows` — the caller decides what an absent window
    means."""
    found = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "rate_limit_event":
            continue
        info = ev.get("rate_limit_info")
        if isinstance(info, dict):
            found = info                            # keep the last one
    return found


def parse_stream(raw: str, *, require_text: bool = True) -> StreamResult | None:
    """The last `result` event in `raw`, or None if there is none.

    `require_text=True` (the sprint agent's rule) only accepts an event that
    carries a `result` key, so a usage-limit message or a bare error envelope
    falls through to the caller's raw-text path. `require_text=False` (the chat
    agent's rule) accepts any `result` event, so an errored turn still yields its
    session id."""
    found = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "result":
            continue
        if require_text and "result" not in ev:
            continue
        found = ev                                  # keep the last one
    if found is None:
        return None
    cost = found.get("total_cost_usd")
    return StreamResult(
        text=str(found.get("result") or ""),
        session_id=str(found.get("session_id") or ""),
        usage=found.get("usage") or {},
        cost=float(cost) if isinstance(cost, (int, float)) else None,
        turns=found.get("num_turns"),
        duration_ms=found.get("duration_ms"),
    )
