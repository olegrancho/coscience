"""The PM heartbeat: gather context, call the reasoner once (fenced behind an
atomic staging commit), then idempotently submit proposed sprints + write the
report. Deterministic and kill-safe; the reasoner does no writes."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass

from coscience.models import Sprint, SprintStatus
from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint, coerce_resources


def context_fingerprint(context: PMContext) -> str:
    """A stable hash of the inputs the PM should react to: program goals, human
    guidance, the state of approved/running/done work, and results. The PM's own
    pending proposals (status 'proposed') are deliberately excluded — they are its
    output, not new input, so proposing does not re-trigger the next cycle."""
    payload = {
        "goals": context.goals,
        "guidance": sorted(context.human_guidance),
        "active": sorted((s["id"], s["status"]) for s in context.open_sprints
                         if s["status"] != SprintStatus.PROPOSED.value),
        "completed": sorted((s["id"], s["result"]) for s in context.completed),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    guidance = [n["text"] for n in substrate.load_guidance(program_id)]
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        open_sprints=open_sprints, completed=completed,
        prior_proposals=list(pm.proposed_ids),
        human_guidance=guidance,
    )


@dataclass
class StagedCycle:
    cycle: int
    output: PMCycleOutput
    fingerprint: str = ""


def proposal_id(program_id: str, cycle: int, suffix: str) -> str:
    return f"{program_id}-c{cycle}-{suffix}"


def _staging_path(substrate, program_id: str):
    return substrate.program_dir(program_id) / ".pm" / "cycle-staging.json"


def write_staging(substrate, program_id: str, cycle: int, output: PMCycleOutput,
                  fingerprint: str = "") -> None:
    path = _staging_path(substrate, program_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cycle": cycle,
        "fingerprint": fingerprint,
        "report": output.report,
        "proposals": [
            {"suffix": p.suffix, "goals": p.goals, "plan": p.plan,
             "priority": p.priority, "resources_required": p.resources_required,
             "rationale": p.rationale, "title": p.title, "summary": p.summary}
            for p in output.proposals
        ],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)  # atomic on POSIX


def read_staging(substrate, program_id: str) -> "StagedCycle | None":
    path = _staging_path(substrate, program_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    output = PMCycleOutput(
        report=data.get("report", ""),
        proposals=[ProposedSprint(**p) for p in data.get("proposals", [])],
    )
    return StagedCycle(cycle=int(data["cycle"]), output=output,
                       fingerprint=data.get("fingerprint", ""))


def clear_staging(substrate, program_id: str) -> None:
    path = _staging_path(substrate, program_id)
    if path.is_file():
        path.unlink()


def pm_beat(substrate, program_id: str, reasoner, now: float | None = None) -> dict:
    """Run one bounded, kill-safe PM cycle for a program. Returns a summary."""
    pm = substrate.load_pm_state(program_id)

    staged = read_staging(substrate, program_id)
    if staged is None:
        cycle = pm.cycle
        context = gather_context(substrate, program_id)
        fingerprint = context_fingerprint(context)
        if fingerprint == pm.last_fingerprint:
            # Event-driven: nothing the PM acts on has changed since the last cycle
            # (no new results, guidance, approvals or goal edits). Stay idle — don't
            # burn a reasoner call or pile up redundant proposals.
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            return {"program": program_id, "cycle": cycle,
                    "submitted": [], "proposed": [], "skipped": True}
        output = reasoner.run(context)                 # the ONE reasoner call
        write_staging(substrate, program_id, cycle, output, fingerprint)  # COMMIT POINT
        staged = StagedCycle(cycle=cycle, output=output, fingerprint=fingerprint)

    cycle = staged.cycle
    submitted: list[str] = []
    proposed: list[str] = []
    for prop in staged.output.proposals:
        sid = proposal_id(program_id, cycle, prop.suffix)
        proposed.append(sid)
        already_proposed = sid in pm.proposed_ids
        if not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            substrate.save_sprint(Sprint(
                id=sid, status=SprintStatus.PROPOSED, goals=prop.goals,
                plan=list(prop.plan),
                program=program_id, priority=prop.priority,
                resources_required=coerce_resources(prop.resources_required),
                rationale=prop.rationale,
                title=prop.title,
                summary=prop.summary,
            ))
        if not already_proposed:
            submitted.append(sid)                      # new this run

    substrate.save_report(program_id, staged.output.report)

    pm.cycle = cycle + 1
    pm.last_run = time.time() if now is None else now
    pm.last_fingerprint = staged.fingerprint
    for sid in proposed:
        if sid not in pm.proposed_ids:
            pm.proposed_ids.append(sid)
    pm.log.append(f"cycle {cycle}: proposed {proposed}")
    substrate.save_pm_state(pm)

    clear_staging(substrate, program_id)
    return {"program": program_id, "cycle": cycle,
            "submitted": submitted, "proposed": proposed, "skipped": False}
