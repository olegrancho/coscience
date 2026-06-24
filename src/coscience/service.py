"""Transport-agnostic service API over the substrate + ledger.

Every method returns JSON-serialisable plain data so the MCP and HTTP layers
can hand results straight to clients.
"""
from __future__ import annotations

from pathlib import Path

from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool, load_pool
from coscience.substrate import Substrate


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
                "id": lease.id, "amounts": lease.amounts,
                "granted_at": lease.granted_at, "expires_at": lease.expires_at,
                "priority": lease.priority, "preemptible": lease.preemptible,
            },
        }
