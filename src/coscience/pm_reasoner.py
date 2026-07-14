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
    if not isinstance(raw, dict):   # LLM may emit a string/list here — treat as empty, don't crash
        return out
    for k, v in raw.items():
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
    failed: list[dict] = field(default_factory=list)   # sprints that failed, with why
    sprint_feedback: list[dict] = field(default_factory=list)  # human notes addressed to the PM
    prior_proposals: list[str] = field(default_factory=list)
    human_guidance: list[str] = field(default_factory=list)
    ideas: list[dict] = field(default_factory=list)   # current idea pool (id/text/source/protected/demoted)
    idea_feedback: list[dict] = field(default_factory=list)  # open human-last threads on pool ideas (always target "pm")
    proposed_count: int = 0                            # sprints already in 'proposed'
    max_proposed: int = 4                              # cap the PM may not exceed
    model: str = ""                                    # Claude model for this PM cycle; "" = default
    workdir: str = ""                                  # resolved cwd for the reasoner's claude session ("" = inherit)

    @property
    def free_slots(self) -> int:
        return max(0, self.max_proposed - self.proposed_count)


@dataclass
class ProposedSprint:
    suffix: str
    goals: str
    plan: list[str] = field(default_factory=list)   # suggested steps (guidance), not commands
    priority: int = 0
    resources_required: dict | None = None
    rationale: str = ""
    title: str = ""
    summary: str = ""
    from_idea: str = ""                              # idea id this promotes (removed when sprint is created)
    model: str = ""                                  # suggested worker model for this sprint; "" = default


@dataclass
class PMCycleOutput:
    proposals: list[ProposedSprint] = field(default_factory=list)
    report: str = ""
    ideas_summary: str = ""                          # PM's narrative over the whole idea pool
    new_ideas: list[str] = field(default_factory=list)      # vague directions to record (PM-sourced)
    delete_idea_ids: list[str] = field(default_factory=list)  # PM ideas to prune (protected ones ignored)
    sprint_edits: list[dict] = field(default_factory=list)   # revisions to still-proposed sprints (from PM feedback)
    reopen_ids: list[str] = field(default_factory=list)      # approved sprints to send back to proposed (now obsolete)
    release_ids: list[str] = field(default_factory=list)     # approved sprints to release into production (-> queued)
    thread_replies: list[dict] = field(default_factory=list)  # [{thread_id, text}] PM answers to open feedback threads


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
