"""Free disk space, per machine (B1/B2).

On 2026-09-19 this platform's root filesystem filled. Every PM cycle and every
dispatcher cycle failed for about forty minutes, the usage rail went blank because its
reading is written to the same disk, and twelve hours later the substrate's git repo was
still refusing to commit because objects written during the outage had never landed.
Nothing anywhere said "the disk is full": the only evidence was in a log file nobody
reads.

Two thresholds, and they mean different things. `LOW_GB` is a warning — someone should
look. `CRITICAL_GB` is a gate: below it a machine takes no new work, because a full disk
does not fail a sprint honestly, it corrupts whatever was mid-write."""
from __future__ import annotations

import os

LOW_GB = 2.0         # warn: the dashboard says so, nothing changes
CRITICAL_GB = 0.5    # gate: this machine takes no new work

_GB = 1024.0 ** 3


def free_gb(path) -> float | None:
    """Free space in GB on the filesystem holding `path`, or None if it cannot be
    read. Uses the unprivileged figure (`f_bavail`), not the root reserve: a worker
    runs as an ordinary user, so the space root could still use is not space it has.
    """
    try:
        st = os.statvfs(str(path))
    except OSError:
        return None
    return (st.f_bavail * st.f_frsize) / _GB


def level(free: float | None) -> str:
    """"" (fine), "low" (warn) or "critical" (gate). An unreadable reading is "": the
    platform never gates on a measurement it does not have."""
    if free is None:
        return ""
    if free < CRITICAL_GB:
        return "critical"
    if free < LOW_GB:
        return "low"
    return ""


def describe(free: float | None) -> str:
    """One line for a human, or "" when there is nothing to say."""
    lv = level(free)
    if not lv or free is None:
        return ""
    amount = f"{free * 1024:.0f} MB" if free < 1 else f"{free:.1f} GB"
    if lv == "critical":
        return f"{amount} free — too little to work safely; taking no new work"
    return f"{amount} free — running low"
