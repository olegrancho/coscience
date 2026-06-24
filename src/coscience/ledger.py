"""Authoritative resource ledger: who holds what, with all-or-nothing grants."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from coscience.models import Lease
from coscience.resources import ResourcePool


class Ledger:
    def __init__(self, pool: ResourcePool, path: Path):
        self.pool = pool
        self.path = Path(path)
        self._leases: dict[str, Lease] = {}
        self._keys_ever_leased: set[str] = set()  # Track keys that have been part of any lease

    # --- persistence ---
    def load(self) -> None:
        if self.path.is_file():
            data = json.loads(self.path.read_text())
            self._leases = {d["sprint_id"]: Lease(**d) for d in data}
        else:
            self._leases = {}
        # Rebuild the set of keys that have been leased
        self._keys_ever_leased.clear()
        for lease in self._leases.values():
            self._keys_ever_leased.update(lease.amounts.keys())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(lease) for lease in self._leases.values()]
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.path)

    # --- queries ---
    def all_leases(self) -> list[Lease]:
        return list(self._leases.values())

    def lease_for(self, sprint_id: str) -> Lease | None:
        return self._leases.get(sprint_id)

    def used(self) -> dict[str, float]:
        # Return all keys that have been part of any lease (past or current)
        out = {k: 0.0 for k in self._keys_ever_leased}
        for lease in self._leases.values():
            for k, v in lease.amounts.items():
                out[k] = out.get(k, 0.0) + v
        return out

    def available(self) -> dict[str, float]:
        used = self.used()
        return {k: cap - used.get(k, 0.0) for k, cap in self.pool.capacity.items()}

    def can_fit(self, amounts: dict[str, float]) -> bool:
        avail = self.available()
        return all(avail.get(k, 0.0) >= v for k, v in amounts.items())

    # --- mutations ---
    def acquire(self, sprint_id, amounts, now, ttl, priority=0, preemptible=True):
        existing = self._leases.get(sprint_id)
        if existing is not None:
            return existing
        if not self.can_fit(amounts):
            return None
        lease = Lease(
            id=uuid.uuid4().hex[:12],
            sprint_id=sprint_id,
            amounts={str(k): float(v) for k, v in amounts.items()},
            granted_at=float(now),
            expires_at=float(now) + float(ttl),
            priority=int(priority),
            preemptible=bool(preemptible),
        )
        # Track that these keys have been leased
        self._keys_ever_leased.update(lease.amounts.keys())
        self._leases[sprint_id] = lease
        self.save()
        return lease

    def release(self, sprint_id: str) -> None:
        if sprint_id in self._leases:
            del self._leases[sprint_id]
            self.save()

    def renew(self, sprint_id, now, ttl) -> None:
        lease = self._leases.get(sprint_id)
        if lease is not None:
            lease.expires_at = float(now) + float(ttl)
            self.save()

    def expire(self, now) -> list[Lease]:
        stale = [l for l in self._leases.values() if l.expires_at <= float(now)]
        for lease in stale:
            del self._leases[lease.sprint_id]
        if stale:
            self.save()
        return stale
