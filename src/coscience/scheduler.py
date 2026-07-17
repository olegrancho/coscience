"""Deterministic scheduling policy over the ledger."""
from __future__ import annotations

from dataclasses import dataclass

from coscience.ledger import Ledger
from coscience.models import Lease, Sprint


@dataclass
class SchedulerPolicy:
    default_ttl: float = 3600.0
    aging_interval: float = 300.0

    def effective_priority(self, sprint: Sprint, queued_at: float, now: float) -> int:
        if self.aging_interval <= 0:
            return sprint.priority
        return sprint.priority + int((now - queued_at) // self.aging_interval)

    def select_grants(self, candidates, queued_at, ledger: Ledger, now) -> list[Sprint]:
        avail = dict(ledger.available())

        def sort_key(s: Sprint):
            return (-self.effective_priority(s, queued_at.get(s.id, now), now),
                    queued_at.get(s.id, now))

        granted: list[Sprint] = []
        for sprint in sorted(candidates, key=sort_key):
            if all(avail.get(k, 0.0) >= v for k, v in sprint.resources_required.items()):
                for k, v in sprint.resources_required.items():
                    avail[k] = avail.get(k, 0.0) - v
                granted.append(sprint)
        return granted

    def select_yield_victims(self, candidate, candidate_priority, ledger: Ledger,
                             yieldable_ids):
        """Leases to hibernate so `candidate` can be granted. Only leases in
        `yieldable_ids` (at a safe yield point — no running agent, no live job)
        are considered; the caller (dispatcher) computes that set. Picks the
        lowest-priority preemptible holders below the candidate's priority, just
        enough to cover the deficit; returns [] if the safe-point set can't cover
        it (nothing is killed — the candidate waits for a job/turn to finish)."""
        need = candidate.resources_required
        avail = dict(ledger.available())
        deficit = {k: v - avail.get(k, 0.0) for k, v in need.items()
                   if v - avail.get(k, 0.0) > 0}
        if not deficit:
            return []

        eligible = [l for l in ledger.all_leases()
                    if l.preemptible and l.priority < candidate_priority
                    and l.sprint_id in yieldable_ids]
        # lowest priority first; tie -> most-recently granted first
        eligible.sort(key=lambda l: (l.priority, -l.granted_at))

        victims: list[Lease] = []
        freed: dict[str, float] = {}
        for lease in eligible:
            if all(freed.get(k, 0.0) >= d for k, d in deficit.items()):
                break
            victims.append(lease)
            for k, v in lease.amounts.items():
                freed[k] = freed.get(k, 0.0) + v

        if all(freed.get(k, 0.0) >= d for k, d in deficit.items()):
            return victims
        return []
