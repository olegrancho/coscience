"""The PM heartbeat: gather context, call the reasoner once (fenced behind an
atomic staging commit), then idempotently submit proposed sprints + write the
report. Deterministic and kill-safe; the reasoner does no writes."""
from __future__ import annotations

from coscience.models import SprintStatus
from coscience.pm_reasoner import PMContext


def gather_context(substrate, program_id: str) -> PMContext:
    program = substrate.load_program(program_id)
    pm = substrate.load_pm_state(program_id)
    open_sprints: list[dict] = []
    completed: list[dict] = []
    for s in substrate.iter_sprints():
        if s.program != program_id:
            continue
        if s.status == SprintStatus.DONE:
            result = ""
            if s.results:
                try:
                    result = substrate.load_result(s.results[0]).summary
                except OSError:
                    result = ""
            completed.append({"id": s.id, "goals": s.goals, "result": result})
        elif s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED,
                          SprintStatus.EXECUTING):
            open_sprints.append({"id": s.id, "status": s.status.value, "goals": s.goals})
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        open_sprints=open_sprints, completed=completed,
        prior_proposals=list(pm.proposed_ids),
    )
