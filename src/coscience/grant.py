"""Delegated approval (todo M1): a person grants the planner the authority to approve
proposed sprints in one program, until a limit they chose is reached.

`proposed -> approved` is otherwise a human decision (docs/sprint-lifecycle.md). A
grant moves that decision to the planner for a bounded stretch, and the bound is
enforced here, at every approval, not trusted to the planner. Four limits:

    sprints   N approvals, then it ends
    until     a wall-clock deadline
    window5h  the current 5-hour usage window: it ends when the window resets or
              when its use reaches the planner's own stop (80%), whichever is first
    week      the same for the weekly window (its stop is 99%)
    paced     no end of its own (M3): approvals are allowed only while weekly usage is
              below the share of the weekly window that has passed, so a busy stretch
              pauses them until time catches up; it runs until revoked

The grant lives in program.md as `approval_grant`; one at a time per program. When it
ends, it says so — `ended_at` and `end_reason` stay on the program and the dashboard
shows them until someone dismisses the notice — so nobody finds out weeks later that
the planner stopped approving. Every approval made under a grant is recorded on the
sprint (`action: "approve (grant)"`) and in the grant's `approved` list."""
from __future__ import annotations

import time
import uuid

LIMITS = ("sprints", "until", "window5h", "week", "paced")
WINDOW_KEY = {"window5h": "5h", "week": "week"}
WEEK = 7 * 86400.0


def _stops(repo_root=None) -> dict[str, float]:
    """The planner's own lines (G1): a window grant ends where the planner would stop."""
    from coscience import usage_gates
    if repo_root is None:
        return dict(usage_gates.DEFAULTS["pm"])
    five, week = usage_gates.limits(repo_root, "pm")
    return {"5h": five, "week": week}


def _windows() -> dict:
    from coscience import usage_meter
    return (usage_meter.read_budget() or {}).get("windows") or {}


def new(by: str, limit: str, *, sprints: int = 0, until: float = 0.0,
        now: float | None = None, windows: dict | None = None) -> dict:
    """A fresh grant. Raises ValueError for a limit that would never end it."""
    now = time.time() if now is None else now
    if limit not in LIMITS:
        raise ValueError(f"limit must be one of {', '.join(LIMITS)}")
    g = {"id": uuid.uuid4().hex[:8], "by": by, "at": now, "limit": limit,
         "approved": [], "ended_at": None, "end_reason": "", "dismissed": False}
    if limit == "sprints":
        if int(sprints) < 1:
            raise ValueError("grant at least one approval")
        g["sprints"] = int(sprints)
    elif limit == "until":
        if float(until) <= now:
            raise ValueError("the deadline must be in the future")
        g["until"] = float(until)
    elif limit == "paced":
        pass                      # bounded per approval by the pace, not by an end
    else:
        # Ride the window that is open now: record when it resets, so the grant ends
        # then even if the usage never reaches the stop.
        w = (windows if windows is not None else _windows()).get(WINDOW_KEY[limit]) or {}
        if not w.get("resets_at"):
            raise ValueError("no current usage reading for that window; pick another limit")
        g["until"] = float(w["resets_at"])
    return g


def end_reason(g: dict, now: float, windows: dict | None = None, repo_root=None) -> str:
    """Why this grant is over now, or "" while it is live."""
    if not g:
        return "no grant"
    if g.get("ended_at"):
        return g.get("end_reason") or "ended"
    limit = g.get("limit")
    if limit == "sprints" and len(g.get("approved") or []) >= int(g.get("sprints") or 0):
        return f"approved {int(g.get('sprints') or 0)} sprint(s), its limit"
    if limit in ("until", "window5h", "week") and now >= float(g.get("until") or 0):
        return {"until": "its deadline passed", "window5h": "the 5-hour usage window reset",
                "week": "the weekly usage window reset"}[limit]
    if limit in WINDOW_KEY:
        key = WINDOW_KEY[limit]
        w = (windows if windows is not None else _windows()).get(key) or {}
        stop = _stops(repo_root)[key]
        if w.get("pct") is not None and float(w["pct"]) >= stop:
            return f"the {'5-hour' if key == '5h' else 'weekly'} usage window reached {stop:g}%"
    return ""


def pace(now: float, windows: dict | None = None) -> dict | None:
    """Weekly usage against the week's progress, both in percent: {used, elapsed}. None
    without a weekly reading that says when the window resets."""
    w = (windows if windows is not None else _windows()).get("week") or {}
    if w.get("pct") is None or not w.get("resets_at"):
        return None
    left = float(w["resets_at"]) - now
    elapsed = min(100.0, max(0.0, (1.0 - left / WEEK) * 100.0))
    return {"used": float(w["pct"]), "elapsed": elapsed}


def hold_reason(g: dict, now: float, windows: dict | None = None) -> str:
    """Why a live grant may not approve right now, or "" when it may. Only a paced grant
    is ever held: while weekly usage is at or ahead of the week's progress (M3)."""
    if not g or g.get("limit") != "paced":
        return ""
    p = pace(now, windows)
    if p is None:
        return "there is no weekly usage reading to pace against"
    if p["used"] >= p["elapsed"]:
        return (f"weekly usage ({p['used']:.0f}%) is ahead of the week "
                f"({p['elapsed']:.0f}% gone)")
    return ""


def is_live(g: dict, now: float, windows: dict | None = None, repo_root=None) -> bool:
    return bool(g) and not end_reason(g, now, windows, repo_root)


def close(g: dict, now: float, reason: str) -> dict:
    """End the grant, loudly: the reason and time stay until someone dismisses them."""
    if g and not g.get("ended_at"):
        g["ended_at"], g["end_reason"] = now, reason
    return g


def refresh(substrate, program_id: str, now: float | None = None,
            windows: dict | None = None) -> dict:
    """Record a grant's end the moment it is over, whoever looks first — the planner's
    cycle or the dashboard — so the end is written down, not inferred later."""
    now = time.time() if now is None else now
    program = substrate.load_program(program_id)
    g = program.approval_grant
    if g and not g.get("ended_at"):
        why = end_reason(g, now, windows, substrate.repo_root)
        if why:
            close(g, now, why)
            substrate.save_program(program)
    return g


def remaining(g: dict, now: float, windows: dict | None = None) -> str:
    """What is left of a live grant, in words, for the dashboard and the planner."""
    limit = g.get("limit")
    if limit == "paced":
        p = pace(now, windows)
        if p is None:
            return "paced to the week, but there is no weekly usage reading now"
        state = ("on pace, so approving" if p["used"] < p["elapsed"]
                 else "ahead of pace, so not approving until the week catches up")
        return (f"paced to the week: {p['used']:.0f}% of weekly usage used, "
                f"{p['elapsed']:.0f}% of the week gone — {state}")
    if limit == "sprints":
        left = int(g.get("sprints") or 0) - len(g.get("approved") or [])
        return f"{left} of {int(g.get('sprints') or 0)} approvals left"
    secs = max(0.0, float(g.get("until") or 0) - now)
    hours = secs / 3600
    when = f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} days"
    return {"until": f"until its deadline, {when} from now",
            "window5h": f"until the 5-hour usage window resets ({when}) or reaches its stop",
            "week": f"until the weekly usage window resets ({when}) or reaches its stop"}.get(limit, "")
