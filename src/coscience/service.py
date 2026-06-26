"""Transport-agnostic service API over the substrate + ledger.

Every method returns JSON-serialisable plain data so the MCP and HTTP layers
can hand results straight to clients.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus, Step, Program, ProgramStatus
from coscience.resources import ResourcePool, load_pool
from coscience.substrate import Substrate


def service_from_env() -> "Service":
    """Construct a Service from COSCIENCE_REPO (default: current directory)."""
    repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))
    return Service(repo_root)


class NotFoundError(KeyError):
    """A requested sprint or result does not exist."""


class Service:
    def __init__(self, repo_root, pool: ResourcePool | None = None):
        self.repo_root = Path(repo_root)
        self.substrate = Substrate(self.repo_root)
        self.pool = pool if pool is not None else load_pool(self.repo_root)

    def _ledger(self) -> Ledger:
        ledger = Ledger(self.pool, self.repo_root / ".coscience" / "leases.json")
        ledger.load()
        return ledger

    def _load_sprint(self, sprint_id: str) -> Sprint:
        if not (self.substrate.sprint_dir(sprint_id) / "sprint.md").is_file():
            raise NotFoundError(sprint_id)
        return self.substrate.load_sprint(sprint_id)

    # --- sprints ---
    def submit_sprint(self, *, id: str, goals: str, plan: list[dict],
                      program: str | None = None, priority: int = 0,
                      preemptible: bool = True, resources_required: dict | None = None,
                      status: str = "proposed") -> str:
        if not plan:
            raise ValueError("plan must have at least one step")
        if (self.substrate.sprint_dir(id) / "sprint.md").is_file():
            raise ValueError(f"sprint {id} already exists")
        sprint = Sprint(
            id=id,
            status=SprintStatus(status),
            goals=goals,
            plan=[Step.from_dict(step) for step in plan],
            program=program,
            resources_required={k: float(v) for k, v in (resources_required or {}).items()},
            priority=priority,
            preemptible=preemptible,
        )
        self.substrate.save_sprint(sprint)
        return id

    def approve_sprint(self, sprint_id: str) -> None:
        sprint = self._load_sprint(sprint_id)
        sprint.status = SprintStatus.APPROVED
        self.substrate.save_sprint(sprint)

    def reject_sprint(self, sprint_id: str) -> None:
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PROPOSED:
            raise ValueError(f"can only reject a proposed sprint; {sprint_id} is {sprint.status.value}")
        sprint.status = SprintStatus.CANCELED
        self.substrate.save_sprint(sprint)

    def edit_sprint(self, sprint_id: str, *, goals=None, plan=None, priority=None,
                    resources_required=None, preemptible=None) -> None:
        sprint = self._load_sprint(sprint_id)
        st = sprint.status
        if st in (SprintStatus.DONE, SprintStatus.CANCELED):
            raise ValueError(f"{sprint_id} is {st.value} and is read-only")
        if (goals is not None or plan is not None) and st != SprintStatus.PROPOSED:
            raise ValueError("goals/plan are editable only while proposed")
        if plan is not None and len(plan) == 0:
            raise ValueError("plan must have at least one step")
        if goals is not None:
            sprint.goals = goals
        if plan is not None:
            sprint.plan = [Step.from_dict(s) for s in plan]
        if priority is not None:
            sprint.priority = priority
        if resources_required is not None:
            sprint.resources_required = {k: float(v) for k, v in resources_required.items()}
        if preemptible is not None:
            sprint.preemptible = preemptible
        self.substrate.save_sprint(sprint)

    def list_sprints(self, status: str | None = None) -> list[dict]:
        wanted = SprintStatus(status) if status is not None else None
        rows = []
        for sprint in self.substrate.iter_sprints(status=wanted):
            rows.append({
                "id": sprint.id,
                "status": sprint.status.value,
                "goals": sprint.goals,
                "priority": sprint.priority,
                "steps": len(sprint.plan),
                "results": list(sprint.results),
            })
        return rows

    def get_sprint(self, sprint_id: str) -> dict:
        sprint = self._load_sprint(sprint_id)
        progress = self.substrate.load_progress(sprint_id)
        lease = self._ledger().lease_for(sprint_id)
        return {
            "id": sprint.id,
            "status": sprint.status.value,
            "goals": sprint.goals,
            "priority": sprint.priority,
            "preemptible": sprint.preemptible,
            "resources_required": sprint.resources_required,
            "plan": [{"id": s.id, "run": s.run} for s in sprint.plan],
            "completed_steps": progress.completed_steps,
            "detached": progress.detached,
            "outputs": progress.outputs,
            "lease": None if lease is None else {
                "id": lease.id, "sprint_id": lease.sprint_id, "amounts": lease.amounts,
                "granted_at": lease.granted_at, "expires_at": lease.expires_at,
                "priority": lease.priority, "preemptible": lease.preemptible,
            },
        }

    # --- programs (read-only) ---
    def list_programs(self, status: str | None = None) -> list[dict]:
        wanted = ProgramStatus(status) if status is not None else None
        return [{"id": p.id, "title": p.title, "status": p.status.value, "goals": p.goals}
                for p in self.substrate.iter_programs(status=wanted)]

    def get_program(self, program_id: str) -> dict:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        p = self.substrate.load_program(program_id)
        pm = self.substrate.load_pm_state(program_id)
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        return {
            "id": p.id, "title": p.title, "status": p.status.value, "goals": p.goals,
            "report": self.substrate.load_report(program_id),
            "cycle": pm.cycle,
            "sprints": [{"id": s.id, "status": s.status.value, "goals": s.goals}
                        for s in sprints],
        }

    def set_program_status(self, program_id: str, status: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        new_status = ProgramStatus(status)  # raises ValueError on a bad value
        program = self.substrate.load_program(program_id)
        program.status = new_status
        self.substrate.save_program(program)

    def _require_program(self, program_id: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)

    def list_guidance(self, program_id: str) -> list[dict]:
        self._require_program(program_id)
        return self.substrate.load_guidance(program_id)

    def add_guidance(self, program_id: str, text: str) -> dict:
        self._require_program(program_id)
        notes = self.substrate.load_guidance(program_id)
        note = {"id": uuid4().hex[:8], "text": text, "added_at": time.time()}
        notes.append(note)
        self.substrate.save_guidance(program_id, notes)
        return note

    def remove_guidance(self, program_id: str, note_id: str) -> None:
        self._require_program(program_id)
        notes = [n for n in self.substrate.load_guidance(program_id) if n["id"] != note_id]
        self.substrate.save_guidance(program_id, notes)

    # --- results ---
    def list_results(self) -> list[dict]:
        return [{"id": r.id, "sprint": r.sprint, "summary": r.summary}
                for r in self.substrate.iter_results()]

    def get_result(self, result_id: str) -> dict:
        if not (self.repo_root / "results" / f"{result_id}.md").is_file():
            raise NotFoundError(result_id)
        r = self.substrate.load_result(result_id)
        return {"id": r.id, "sprint": r.sprint, "summary": r.summary}

    # --- ledger ---
    def ledger_status(self) -> dict:
        ledger = self._ledger()
        return {
            "capacity": dict(self.pool.capacity),
            "used": ledger.used(),
            "available": ledger.available(),
            "leases": [
                {"id": l.id, "sprint_id": l.sprint_id, "amounts": l.amounts,
                 "granted_at": l.granted_at, "expires_at": l.expires_at,
                 "priority": l.priority, "preemptible": l.preemptible}
                for l in ledger.all_leases()
            ],
        }
