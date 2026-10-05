"""Where each kind of agent stops launching, as a share of Claude's usage windows (G1).

Usage is a fixed subscription window, not a bill: the scarce thing is the share left
for a human who wants a chat or a forced replan. So autonomous launches stand down
early, each kind at its own line in the 5-hour window, and all of them near the top of
the weekly one. Human-triggered paths (chat, a forced replan) keep the full 100.

The lines are set from Compute -> Claude usage and kept in one substrate file the
backend, the PM loop and the dispatch loop all read on every check — three processes
have to agree, as with the pause marker — so a change applies on the next beat with
no restart. An unset or unreadable value falls back to its default."""
from __future__ import annotations

import json
from pathlib import Path

KINDS = ("pm", "worker", "wiki")
WINDOWS = ("5h", "week")
DEFAULTS: dict[str, dict[str, float]] = {
    "pm": {"5h": 80.0, "week": 99.0},       # planner beats, catch-up reports
    "worker": {"5h": 90.0, "week": 99.0},   # sprint agents
    "wiki": {"5h": 70.0, "week": 99.0},     # wiki runs, below both: nothing waits on them
}


def _path(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "usage-gates.json"


def _valid(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 1.0 <= f <= 100.0 else None


def load(repo_root) -> dict[str, dict[str, float]]:
    """Every kind's lines, defaults filled in for anything unset or unreadable."""
    try:
        raw = json.loads(_path(repo_root).read_text())
    except (OSError, ValueError):
        raw = {}
    out = {}
    for kind in KINDS:
        got = raw.get(kind) if isinstance(raw, dict) and isinstance(raw.get(kind), dict) else {}
        out[kind] = {w: (_valid(got.get(w)) or DEFAULTS[kind][w]) for w in WINDOWS}
    return out


def limits(repo_root, kind: str) -> tuple[float, float]:
    """(5-hour line, weekly line) for one kind of agent."""
    g = load(repo_root)[kind]
    return g["5h"], g["week"]


def save(repo_root, gates: dict) -> dict[str, dict[str, float]]:
    """Replace the lines. Raises ValueError naming the first value out of 1-100."""
    clean = {}
    for kind in KINDS:
        given = gates.get(kind) or {}
        clean[kind] = {}
        for w in WINDOWS:
            v = given.get(w, DEFAULTS[kind][w])
            if _valid(v) is None:
                raise ValueError(f"{kind} {w}: a percentage from 1 to 100")
            clean[kind][w] = float(v)
    p = _path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n")
    tmp.replace(p)
    return clean
