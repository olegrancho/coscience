"""Typed domain models for the Phase 0 substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProgramStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class SprintStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    DONE = "done"
    CANCELED = "canceled"


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


@dataclass
class Program:
    id: str
    title: str
    goals: str
    status: ProgramStatus = ProgramStatus.ACTIVE


@dataclass
class PMState:
    program_id: str
    cycle: int = 0
    last_run: float | None = None
    proposed_ids: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    last_fingerprint: str = ""
