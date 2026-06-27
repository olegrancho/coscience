"""The PM reasoner seam: the LLM (or a fake) returns structured data; the PM
machinery performs every substrate write. Keeping writes out of the reasoner is
what makes propose-only and idempotency enforceable in tested Python."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


def coerce_resources(raw) -> dict[str, float]:
    """Turn a reasoner's resources_required into a clean {name: amount} map.

    The LLM is free-form: it sometimes emits non-numeric values (e.g. a note like
    'CPU-bound; ~30 min wall clock') or extra keys. Keep only entries whose value
    is a real number; silently drop the rest rather than crashing the PM cycle."""
    out: dict[str, float] = {}
    for k, v in (raw or {}).items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class PMContext:
    program_id: str
    goals: str
    cycle: int
    open_sprints: list[dict] = field(default_factory=list)
    completed: list[dict] = field(default_factory=list)
    prior_proposals: list[str] = field(default_factory=list)
    human_guidance: list[str] = field(default_factory=list)


@dataclass
class ProposedSprint:
    suffix: str
    goals: str
    plan: list[dict]
    priority: int = 0
    resources_required: dict | None = None
    rationale: str = ""
    title: str = ""
    summary: str = ""


@dataclass
class PMCycleOutput:
    proposals: list[ProposedSprint] = field(default_factory=list)
    report: str = ""


class Reasoner(Protocol):
    def run(self, context: PMContext) -> PMCycleOutput:
        ...


class FakeReasoner:
    """Deterministic reasoner for tests: returns the given outputs in order,
    then empty outputs. Records each call's context in `.calls`."""

    def __init__(self, outputs: list[PMCycleOutput]):
        self._outputs = list(outputs)
        self.calls: list[PMContext] = []

    def run(self, context: PMContext) -> PMCycleOutput:
        self.calls.append(context)
        if not self._outputs:
            return PMCycleOutput()
        return self._outputs.pop(0)
