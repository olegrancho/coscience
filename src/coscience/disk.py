"""Free disk space, per machine (B1/B2).

On 2026-09-19 this platform's root filesystem filled. Every PM cycle and every
dispatcher cycle failed for about forty minutes, the usage rail went blank because its
reading is written to the same disk, and twelve hours later the substrate's git repo was
still refusing to commit because objects written during the outage had never landed.
Nothing anywhere said "the disk is full": the only evidence was in a log file nobody
reads.

Two thresholds, and they mean different things. `LOW_GB` is a warning — someone should
look. `CRITICAL_GB` is a gate: below it a machine takes no new work, because a full disk
does not fail a sprint honestly, it corrupts whatever was mid-write.

Both are overridable from the environment, which is how the gate gets rehearsed: the
alternative is filling a real machine's disk, and a server with 90 GB free needs 90 GB of
ballast to test a warning about running out of space. Raising the line instead puts every
machine below it — against real readings, through the real gates, including the remote
ones no local trick can reach — and lowering it again lifts them. A run with the line
moved says so in every message it produces, so an agent reading an absurd reading knows
why, and it is read once at import: a loop that is running does not quietly change its
mind about whether to work.

    COSCIENCE_DISK_CRITICAL_GB=500 coscience dispatch --loop
"""
from __future__ import annotations

import os

DEFAULT_LOW_GB = 5.0       # warn: the dashboard says so, nothing changes
DEFAULT_CRITICAL_GB = 1.0  # gate: this machine takes no new work
LOW_ENV = "COSCIENCE_DISK_LOW_GB"
CRITICAL_ENV = "COSCIENCE_DISK_CRITICAL_GB"


def _threshold(name: str, default: float) -> float:
    """A threshold, from the environment when it is set there. An unreadable value
    falls back to the default rather than raising: a typo in a drill must not take
    down the loop that reads it at import."""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


LOW_GB = _threshold(LOW_ENV, DEFAULT_LOW_GB)
CRITICAL_GB = _threshold(CRITICAL_ENV, DEFAULT_CRITICAL_GB)
# True when this process is not using the real lines, which every message then says.
# Not for the person who moved them — they know — but for the agents: a planner meets
# "238 GB free, too little to work safely" with no way to read an environment variable,
# and without the note it has to invent an explanation. Observed on the first drill: the
# planner read it and correctly declined to act.
DRILL = (LOW_GB, CRITICAL_GB) != (DEFAULT_LOW_GB, DEFAULT_CRITICAL_GB)
_DRILL_NOTE = " [drill: the line was moved by COSCIENCE_DISK_*, this is not a real shortage]"

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
    note = _DRILL_NOTE if DRILL else ""
    if lv == "critical":
        return f"{amount} free — too little to work safely; taking no new work{note}"
    return f"{amount} free — running low{note}"
