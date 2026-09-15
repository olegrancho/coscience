"""Deterministic scheduling policy over the ledger."""
from __future__ import annotations

from dataclasses import dataclass

from coscience.ledger import Ledger
from coscience.models import Lease, Sprint
from coscience.resources import GPU_KEYS, PLATFORM_KEYS, effective_requirement, gpu_request


@dataclass
class SchedulerPolicy:
    default_ttl: float = 3600.0
    aging_interval: float = 300.0

    def effective_priority(self, sprint: Sprint, queued_at: float, now: float) -> int:
        if self.aging_interval <= 0:
            return sprint.priority
        return sprint.priority + int((now - queued_at) // self.aging_interval)

    def select_grants(self, candidates, queued_at, ledger: Ledger, now, pinned=None,
                      readopt=frozenset()) -> list[Sprint]:
        def sort_key(s: Sprint):
            return (-self.effective_priority(s, queued_at.get(s.id, now), now),
                    queued_at.get(s.id, now))

        pinned = pinned or {}
        granted: list[Sprint] = []
        pending: list[tuple[str, dict[str, float], list[int]]] = []
        for sprint in sorted(candidates, key=sort_key):
            need = effective_requirement(sprint.resources_required, ledger.pool)
            # The dispatcher acquires in this same order, so `ledger.acquire` lands
            # each sprint on the host and cards chosen here. A sprint pinned to a
            # host by earlier work is only ever fit there — it waits rather than
            # moving elsewhere. A candidate in `readopt` (its agent or job is still
            # physically running) may still land on its pinned host even when that
            # host is drained or quiet — only new work is kept off it.
            placed = ledger.fit(need, sprint.program, pending, host=pinned.get(sprint.id),
                                readopt=sprint.id in readopt)
            if placed is not None:
                pending.append((placed[0], need, placed[1]))
                granted.append(sprint)
        return granted

    def select_yield_victims(self, candidate, candidate_priority, ledger: Ledger,
                             yieldable_ids, pinned_host=None):
        """Leases to hibernate so `candidate` can be granted. Only leases in
        `yieldable_ids` (at a safe yield point — no running agent, no live job)
        are considered; the caller (dispatcher) computes that set. For each host the
        candidate may use, in order, adds the lowest-priority preemptible holders
        below the candidate's priority that hold something it needs there, one at a
        time, until the candidate fits with them released, then prunes any victim
        that turned out not to be needed — so a card shared by several leases gives
        up only as many as it takes. Returns [] if no host can be cleared (nothing is
        killed — the candidate waits for a job/turn to finish). A pinned candidate
        only ever frees room on its own host."""
        need = effective_requirement(candidate.resources_required, ledger.pool)
        if ledger.fit(need, candidate.program, host=pinned_host) is not None:
            return []

        eligible = [l for l in ledger.all_leases()
                    if l.preemptible and l.priority < candidate_priority
                    and l.sprint_id in yieldable_ids]
        # lowest priority first; tie -> most-recently granted first
        eligible.sort(key=lambda l: (l.priority, -l.granted_at))
        wants_cards = gpu_request(need)[0] > 0

        for h in ledger.pool.grantable_hosts(candidate.program):
            if pinned_host is not None and h.name != pinned_host:
                continue
            victims: list[Lease] = []
            for lease in eligible:
                if not _frees_room_for(lease, need, h.name, wants_cards):
                    continue
                victims.append(lease)
                released = {v.sprint_id for v in victims}
                if ledger.fit(need, candidate.program, exclude=released, host=h.name) is not None:
                    return _prune_victims(victims, need, candidate.program, ledger, h.name)
        return []


def _prune_victims(victims: list[Lease], need: dict[str, float], program: str | None,
                    ledger: Ledger, host: str) -> list[Lease]:
    """Drop victims that turned out not to be needed for the fit to succeed. The
    last-added victim is always needed (the fit failed without it); walk the rest
    from most-recently-added back to the first, dropping any whose exclusion is not
    needed — i.e. the candidate still fits with it NOT released and the other kept
    victims still released."""
    kept = list(victims)
    for lease in reversed(victims[:-1]):
        still_released = {v.sprint_id for v in kept if v is not lease}
        if ledger.fit(need, program, exclude=still_released, host=host) is not None:
            kept.remove(lease)
    return kept


def _frees_room_for(lease: Lease, need: dict[str, float], host: str, wants_cards: bool) -> bool:
    """Whether releasing `lease` gives back anything `need` asks for on `host`."""
    if wants_cards and lease.host == host and lease.gpu_devices:
        return True
    return any(k in need and k not in GPU_KEYS and (k in PLATFORM_KEYS or lease.host == host)
               for k in lease.amounts)
