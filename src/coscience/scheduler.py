"""Deterministic scheduling policy over the ledger."""
from __future__ import annotations

from dataclasses import dataclass

from coscience.ledger import Ledger
from coscience.models import Sprint


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
