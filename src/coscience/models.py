"""Typed domain models for the Phase 0 substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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
class Step:
    id: str
    run: str

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(id=str(d["id"]), run=str(d["run"]))


@dataclass
class StepResult:
    step_id: str
    completed: bool
    output: str = ""


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
    plan: list[Step]
    program: str | None = None
    results: list[str] = field(default_factory=list)
    resources_required: dict[str, float] = field(default_factory=dict)
    priority: int = 0
    preemptible: bool = True


@dataclass
class Result:
    id: str
    sprint: str
    summary: str


@dataclass
class ProgressState:
    sprint_id: str
    completed_steps: list[str] = field(default_factory=list)
    detached: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
