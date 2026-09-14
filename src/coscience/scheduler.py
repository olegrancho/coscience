"""Deterministic scheduling policy over the ledger."""
from __future__ import annotations

from dataclasses import dataclass

from coscience.ledger import Ledger
from coscience.models import Lease, Sprint
from coscience.resources import PLATFORM_KEYS, effective_requirement


@dataclass
class SchedulerPolicy:
    default_ttl: float = 3600.0
    aging_interval: float = 300.0

    def effective_priority(self, sprint: Sprint, queued_at: float, now: float) -> int:
        if self.aging_interval <= 0:
            return sprint.priority
        return sprint.priority + int((now - queued_at) // self.aging_interval)

    def select_grants(self, candidates, queued_at, ledger: Ledger, now) -> list[Sprint]:
        def sort_key(s: Sprint):
            return (-self.effective_priority(s, queued_at.get(s.id, now), now),
                    queued_at.get(s.id, now))

        granted: list[Sprint] = []
        pending: list[tuple[str, dict[str, float]]] = []
        for sprint in sorted(candidates, key=sort_key):
            need = effective_requirement(sprint.resources_required, ledger.pool)
            # The dispatcher acquires in this same order, so `ledger.acquire` lands
            # each sprint on the host chosen here.
            host = ledger.fit_host(need, sprint.program, pending)
            if host is not None:
                pending.append((host, need))
                granted.append(sprint)
        return granted

    def select_yield_victims(self, candidate, candidate_priority, ledger: Ledger,
                             yieldable_ids):
        """Leases to hibernate so `candidate` can be granted. Only leases in
        `yieldable_ids` (at a safe yield point — no running agent, no live job)
        are considered; the caller (dispatcher) computes that set. Tries each host
        the candidate may use, in order: picks the lowest-priority preemptible
        holders below the candidate's priority whose release frees room ON THAT
        host, just enough to cover its deficit. Returns [] if no host can be
        cleared (nothing is killed — the candidate waits for a job/turn to finish)."""
        need = effective_requirement(candidate.resources_required, ledger.pool)
        if ledger.fit_host(need, candidate.program) is not None:
            return []

        for h in ledger.pool.placeable_hosts(candidate.program):
            avail = ledger.available(h.name)
            deficit = {k: v - avail.get(k, 0.0) for k, v in need.items()
                       if v - avail.get(k, 0.0) > 0}
            needs_host_room = any(k not in PLATFORM_KEYS for k in deficit)

            eligible = [l for l in ledger.all_leases()
                        if l.preemptible and l.priority < candidate_priority
                        and l.sprint_id in yieldable_ids
                        and (l.host == h.name or not needs_host_room)]
            # lowest priority first; tie -> most-recently granted first
            eligible.sort(key=lambda l: (l.priority, -l.granted_at))

            victims: list[Lease] = []
            freed: dict[str, float] = {}
            for lease in eligible:
                if all(freed.get(k, 0.0) >= d for k, d in deficit.items()):
                    break
                victims.append(lease)
                for k, v in lease.amounts.items():
                    if k in PLATFORM_KEYS or lease.host == h.name:
                        freed[k] = freed.get(k, 0.0) + v

            if all(freed.get(k, 0.0) >= d for k, d in deficit.items()):
                return victims
        return []
