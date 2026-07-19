"""The dispatcher: a single heartbeat that schedules many sprints over the ledger."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from coscience.ledger import Ledger
from coscience.models import BeatOutcome, SprintStatus, set_status
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.substrate import Substrate
from coscience.worker import Worker

from coscience import artifacts

_ELIGIBLE = (SprintStatus.QUEUED, SprintStatus.EXECUTING, SprintStatus.HIBERNATED)


@dataclass
class CycleReport:
    granted: int = 0
    hibernated: int = 0
    beaten: int = 0
    completed: int = 0
    waiting: int = 0
    reconciled: int = 0


class Dispatcher:
    def __init__(self, substrate: Substrate, agent,
                 pool: ResourcePool, policy: SchedulerPolicy | None = None,
                 usage_gate=None):
        self.substrate = substrate
        self.agent = agent
        self.policy = policy or SchedulerPolicy()
        self.worker = Worker(substrate, agent, usage_gate=usage_gate)
        cos = substrate.repo_root / ".coscience"
        self.ledger = Ledger(pool, cos / "leases.json")
        self._queue_path = cos / "queue.json"

    def _load_queue(self) -> dict[str, float]:
        if self._queue_path.is_file():
            return {str(k): float(v) for k, v in json.loads(self._queue_path.read_text()).items()}
        return {}

    def _save_queue(self, queue: dict[str, float]) -> None:
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._queue_path.with_name(self._queue_path.name + ".tmp")
        tmp.write_text(json.dumps(queue, indent=2))
        tmp.replace(self._queue_path)

    def run_one_cycle(self, now: float | None = None) -> CycleReport:
        now = time.time() if now is None else float(now)
        report = CycleReport()
        ttl = self.policy.default_ttl

        self.ledger.load()
        self.ledger.expire(now)

        eligible = self.substrate.iter_sprints()
        eligible = [s for s in eligible if s.status in _ELIGIBLE]
        eligible_ids = {s.id for s in eligible}

        queue = self._load_queue()
        for s in eligible:
            queue.setdefault(s.id, now)
        queue = {k: v for k, v in queue.items() if k in eligible_ids}

        # --- grants ---
        # A sprint bound to artifacts is grantable only when none of its bound
        # artifacts is locked by another holder (the artifact is a capacity-1
        # resource). Filter those out before the pool scheduler runs.
        needs = [s for s in eligible if self.ledger.lease_for(s.id) is None
                 and not artifacts.sprint_blocked(self.substrate, s)]
        for sprint in self.policy.select_grants(needs, queue, self.ledger, now):
            eff = self.policy.effective_priority(sprint, queue.get(sprint.id, now), now)
            if self.ledger.acquire(sprint.id, sprint.resources_required, now, ttl,
                                   priority=eff, preemptible=sprint.preemptible):
                # Acquire the sprint's artifact locks (instantiating create-targets).
                # If a same-cycle race lost the atomic acquire, give the lease back
                # and leave the sprint queued for a later cycle.
                if not artifacts.acquire_for_sprint(self.substrate, sprint, now):
                    self.ledger.release(sprint.id)
                    continue
                report.granted += 1
                if sprint.status in (SprintStatus.QUEUED, SprintStatus.HIBERNATED):
                    set_status(sprint, SprintStatus.EXECUTING)
                    self.substrate.save_sprint(sprint)

        # --- yield: hibernate a safe-point sprint to free a starved QUEUED candidate ---
        # Cooperative preemption: never hard-kill. Only a QUEUED candidate can
        # trigger a yield (hibernated sprints re-enter from free capacity only, so
        # there is no hibernate ping-pong). Victims are chosen only among leases at
        # a safe yield point (no running agent, no live job); the freed capacity is
        # granted on the NEXT cycle's grant step.
        starved = [s for s in eligible
                   if s.status == SprintStatus.QUEUED and self.ledger.lease_for(s.id) is None]
        if starved:
            starved.sort(
                key=lambda s: -self.policy.effective_priority(s, queue.get(s.id, now), now))
            cand = starved[0]
            cand_eff = self.policy.effective_priority(cand, queue.get(cand.id, now), now)
            yieldable = {l.sprint_id for l in self.ledger.all_leases()
                         if self.worker.is_yieldable(l.sprint_id)}
            victims = self.policy.select_yield_victims(cand, cand_eff, self.ledger, yieldable)
            for v in victims:
                self.ledger.release(v.sprint_id)
                self.worker.hibernate_sprint(self.substrate.load_sprint(v.sprint_id))
                report.hibernated += 1

        # --- reconcile: no lease => no running job ---
        # Grants/preemption above re-adopted any leaseless running sprint that
        # still fits; kill the detached jobs of those that remain leaseless
        # (e.g. expired across a dispatcher outage) so physical use matches the
        # ledger.
        for sprint in eligible:
            if sprint.status != SprintStatus.EXECUTING:
                continue                          # hibernated: intentionally leaseless, no agent/job
            if self.ledger.lease_for(sprint.id) is None:
                # A sleeping sprint (no live agent, but a tracked detached job) must
                # also be reaped when leaseless — else its job runs with no lease.
                if self.worker.agent_running(sprint.id) \
                        or self.substrate.load_progress(sprint.id).job_token:
                    self.worker.stop_sprint(sprint)
                    report.reconciled += 1

        # --- run one beat per leased, executing sprint ---
        for lease in self.ledger.all_leases():
            sprint = self.substrate.load_sprint(lease.sprint_id)
            if sprint.status != SprintStatus.EXECUTING:
                continue
            outcome = self.worker.run_sprint_beat(sprint)
            report.beaten += 1
            self.ledger.renew(lease.sprint_id, now, ttl)
            if outcome == BeatOutcome.COMPLETED:
                self.ledger.release(lease.sprint_id)
                queue.pop(lease.sprint_id, None)
                report.completed += 1

        report.waiting = sum(
            1 for s in eligible if self.ledger.lease_for(s.id) is None)

        # Release chat locks left idle past the inactivity window (cuts a final
        # version), so a walked-away editing session frees the artifact.
        reaped = 0
        for program in self.substrate.iter_programs():
            reaped += len(artifacts.reap_stale_chat_locks(self.substrate, program.id, now))

        self._save_queue(queue)
        if report.granted or report.completed or report.hibernated or report.reconciled or reaped:
            self.substrate.commit("dispatch cycle")
        return report
