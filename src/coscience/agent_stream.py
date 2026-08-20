"""Parse a `claude -p --output-format stream-json` capture.

Pure — no IO. Three callers scan the same JSONL feed for the final `result`
event (the sprint agent, the chat agent, the wiki agent) and each wants a
different thing from it, so this returns the whole record and leaves status
handling to them."""
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
