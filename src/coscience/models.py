"""Typed domain models for the Phase 0 substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProgramStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class SprintStatus(StrEnum):
    PROPOSED = "proposed"      # PM/human suggested it; awaiting review
    APPROVED = "approved"      # human authorized it; held until released to run
    QUEUED = "queued"          # released to the scheduler; runs when a resource slot frees
    EXECUTING = "executing"    # lease granted; the worker agent is running
    DONE = "done"
    CANCELED = "canceled"
    FAILED = "failed"          # agent failed repeatedly; terminal until a human acts


class BeatOutcome(StrEnum):
    IDLE = "idle"
    PROGRESSED = "progressed"
    COMPLETED = "completed"


@dataclass
class Lease:
    id: str
    sprint_id: str
    amounts: dict[str, float]
    granted_at: float
    expires_at: float
    priority: int = 0
    preemptible: bool = True


@dataclass
class Sprint:
    id: str
    status: SprintStatus
    goals: str
    plan: list[str] = field(default_factory=list)   # natural-language suggested steps (guidance)
    program: str | None = None
    results: list[str] = field(default_factory=list)
    resources_required: dict[str, float] = field(default_factory=dict)
    priority: int = 0
    preemptible: bool = True
    rationale: str = ""
    title: str = ""
    summary: str = ""
    created_at: float | None = None   # wall-clock of first save; orders sprints by appearance
    comments: list[dict] = field(default_factory=list)  # human feedback [{id, text, added_at}]
    model: str = ""                   # Claude model for this sprint's worker; "" = launcher default
    votes: list[dict] = field(default_factory=list)  # 👍/👎 signal [{by, value:+1|-1, at}]; one per voter


@dataclass
class Result:
    id: str
    sprint: str
    summary: str
    completed_at: float | None = None


@dataclass
class ProgressState:
    sprint_id: str
    agent_token: str = ""              # detached agent process token; "" when not running
    started_at: float | None = None    # when the current agent run was launched
    failures: int = 0                  # consecutive agent failures (nonzero exit), for the retry cap
    last_error: str = ""               # why the most recent run failed (surfaced to the PM)


@dataclass
class Idea:
    """A short, vague candidate direction for a program. The PM grows a pool of
    these, prunes them as data arrives, and promotes promising ones into sprints.
    `source` is who proposed it; pinning == protecting it from PM deletion."""
    id: str
    text: str
    source: str = "human"               # "pm" | "human"
    pinned: bool = False                # pin == protect
    comments: list[dict] = field(default_factory=list)  # [{id, text, added_at}]
    created_at: float = 0.0
    demoted: bool = False               # demoted from a sprint; PM may not re-promote it

    @property
    def protected(self) -> bool:
        """Whether the PM is forbidden from deleting this idea. Human-proposed,
        pinned, commented-on, or demoted ideas are protected — a demoted idea is a
        human 'do not pursue as a sprint' decision the PM must not undo by deleting."""
        return self.source == "human" or self.pinned or bool(self.comments) or self.demoted


@dataclass
class Program:
    id: str
    title: str
    goals: str
    status: ProgramStatus = ProgramStatus.ACTIVE
    pm_model: str = ""                 # Claude model for this program's PM reasoner; "" = default
    workdir: str = ""                  # project folder this program's agents run in; "" = control repo


@dataclass
class PMState:
    program_id: str
    cycle: int = 0
    last_run: float | None = None
    proposed_ids: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    last_fingerprint: str = ""
    # per-category signatures of the last reasoned context, so the next cycle can
    # name WHAT changed; and a capped timeline of activations for the dashboard.
    last_signals: dict = field(default_factory=dict)
    activations: list[dict] = field(default_factory=list)  # [{at, cycle, triggers, submitted, forced}]
