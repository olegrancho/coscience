"""Authoritative resource ledger: who holds what, with all-or-nothing grants."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterable

from coscience.models import Lease
from coscience.resources import LOCAL, PLATFORM_KEYS, ResourcePool

_LEASE_FIELDS = {f.name for f in fields(Lease)}


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
            self._leases = {d["sprint_id"]: Lease(**{k: v for k, v in d.items()
                                                      if k in _LEASE_FIELDS})
                            for d in data}
        else:
            self._leases = {}
        # Rebuild the set of keys that have been leased
        self._keys_ever_leased.clear()
        for lease in self._leases.values():
            self._keys_ever_leased.update(lease.amounts.keys())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for lease in self._leases.values():
            row = asdict(lease)
            if row.get("host") == LOCAL:
                del row["host"]
            rows.append(row)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(rows, indent=2))
        os.replace(tmp, self.path)

    # --- queries ---
    def all_leases(self) -> list[Lease]:
        return list(self._leases.values())

    def lease_for(self, sprint_id: str) -> Lease | None:
        return self._leases.get(sprint_id)

    def used(self, host: str | None = None) -> dict[str, float]:
        """Amounts held — across the pool, or on one host. Platform keys count on
        every host, since they bound agents on the dispatcher's machine."""
        out = {k: 0.0 for k in self._keys_ever_leased}
        for lease in self._leases.values():
            for k, v in lease.amounts.items():
                if host is not None and k not in PLATFORM_KEYS and lease.host != host:
                    continue
                out[k] = out.get(k, 0.0) + v
        return out

    def available(self, host: str | None = None) -> dict[str, float]:
        if host is None:
            used = self.used()
            return {k: cap - used.get(k, 0.0) for k, cap in self.pool.capacity.items()}
        h = self.pool.host(host)
        if h is None:
            return {}
        used = self.used(host)
        out = {k: cap - used.get(k, 0.0) for k, cap in self.pool.capacity.items()
               if k in PLATFORM_KEYS}
        out.update({k: cap - used.get(k, 0.0) for k, cap in h.capacity.items()})
        return out

    def can_fit(self, amounts: dict[str, float], host: str | None = None) -> bool:
        avail = self.available(host)
        return all(avail.get(k, 0.0) >= v for k, v in amounts.items())

    def fit_host(self, amounts: dict[str, float], program: str | None = None,
                 pending: Iterable[tuple[str, dict[str, float]]] = ()) -> str | None:
        """The first placeable host `program` may use that holds ALL of `amounts`,
        or None. `pending` is (host, amounts) granted this cycle but not yet
        acquired, so one pass of grants never books the same room twice."""
        pending = list(pending)
        for h in self.pool.placeable_hosts(program):
            avail = self.available(h.name)
            for p_host, p_amounts in pending:
                for k, v in p_amounts.items():
                    if k in PLATFORM_KEYS or p_host == h.name:
                        avail[k] = avail.get(k, 0.0) - v
            if all(avail.get(k, 0.0) >= v for k, v in amounts.items()):
                return h.name
        return None

    # --- mutations ---
    def acquire(self, sprint_id, amounts, now, ttl, priority=0, preemptible=True,
                program=None):
        existing = self._leases.get(sprint_id)
        if existing is not None:
            return existing
        host = self.fit_host(amounts, program)
        if host is None:
            return None
        lease = Lease(
            id=uuid.uuid4().hex[:12],
            sprint_id=sprint_id,
            amounts={str(k): float(v) for k, v in amounts.items()},
            granted_at=float(now),
            expires_at=float(now) + float(ttl),
            priority=int(priority),
            preemptible=bool(preemptible),
            host=host,
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

    def release_key(self, sprint_id: str, key: str) -> None:
        """Hand back ONE resource without giving up the lease.

        A sprint asleep on a detached job still holds the cpu and gpu that job is
        using, but not the worker slot, which exists to bound how many agent
        processes run at once and so belongs to the agent, not to the lease.
        Dropping the whole lease instead would be wrong twice: the job's real
        resource use would vanish from the ledger, and the dispatcher's
        no-lease-means-no-running-job reconcile would kill the job."""
        lease = self._leases.get(sprint_id)
        if lease is not None and key in lease.amounts:
            del lease.amounts[key]
            self.save()

    def acquire_key(self, sprint_id: str, key: str, amount: float) -> bool:
        """Take a released key back, or False when it no longer fits.

        False is a normal outcome, not an error: it means someone else took the
        slot while this sprint slept, and the sprint waits for it exactly as a
        queued sprint waits."""
        lease = self._leases.get(sprint_id)
        if lease is None:
            return False
        if key in lease.amounts:
            return True                       # already ours; never charge twice
        if not self.can_fit({key: float(amount)}, lease.host):
            return False
        lease.amounts[key] = float(amount)
        self._keys_ever_leased.add(key)
        self.save()
        return True

    def renew(self, sprint_id, now, ttl, priority=None) -> None:
        lease = self._leases.get(sprint_id)
        if lease is not None:
            lease.expires_at = float(now) + float(ttl)
            if priority is not None:
                lease.priority = int(priority)
            self.save()

    def expire(self, now) -> list[Lease]:
        stale = [l for l in self._leases.values() if l.expires_at <= float(now)]
        for lease in stale:
            del self._leases[lease.sprint_id]
        if stale:
            self.save()
        return stale
