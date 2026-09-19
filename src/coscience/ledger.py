"""Authoritative resource ledger: who holds what, with all-or-nothing grants."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterable

from coscience.models import Lease
from coscience.resources import (GPU_KEY, GPU_KEYS, LOCAL, PLATFORM_KEYS, Host,
                                 ResourcePool, gpu_request)

_LEASE_FIELDS = {f.name for f in fields(Lease)}


def _normalize(pending: Iterable[tuple]) -> list[tuple[str, dict[str, float], list[int]]]:
    """Grants made this cycle but not yet acquired, as (host, amounts, cards). Also
    accepts (host, amounts) for callers that place no GPU."""
    return [(p[0], p[1], list(p[2]) if len(p) > 2 else []) for p in pending]


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
        # Leases written before cards were tracked hold a `gpu` count and no card list.
        # Lend each the lowest free cards on its host, in file order, so a new grant is
        # never handed a card an older job is already using.
        for lease in self._leases.values():
            host = self.pool.host(lease.host)
            if lease.gpu_devices or host is None or not gpu_request(lease.amounts)[0]:
                continue
            lease.gpu_devices = self.pick_gpus(host, lease.amounts) or []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for lease in self._leases.values():
            row = asdict(lease)
            # Omit what older code does not know when it carries nothing, so a lease
            # file stays readable across a rollout or a rollback.
            if row.get("host") == LOCAL:
                del row["host"]
            if not row.get("gpu_devices"):
                row.pop("gpu_devices", None)
            rows.append(row)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(rows, indent=2))
        os.replace(tmp, self.path)

    # --- queries ---
    def all_leases(self) -> list[Lease]:
        return list(self._leases.values())

    def lease_for(self, sprint_id: str) -> Lease | None:
        return self._leases.get(sprint_id)

    def _stranded(self, lease: Lease) -> bool:
        host = self.pool.host(lease.host)
        return host is None or not host.placeable

    def stranded(self) -> list[Lease]:
        """Leases on a host the pool no longer has or no longer places on. They keep
        their sprint's work alive but no longer count against pool-wide totals, which
        would otherwise go negative."""
        return [l for l in self._leases.values() if self._stranded(l)]

    def used(self, host: str | None = None) -> dict[str, float]:
        """Amounts held — across the pool, or on one host. Platform keys count on
        every host, since they bound agents on the dispatcher's machine. `gpu` counts
        cards held: a card lent out in shares is one card in use. A stranded lease
        (its host left the pool, or the pool no longer places there) keeps its
        platform-key amounts — those bound agents on the dispatcher's own machine,
        wherever the lease's host was — but drops out of pool-wide totals otherwise,
        so a removed host's capacity leaving the pool doesn't drive use negative."""
        out = {k: 0.0 for k in self._keys_ever_leased if k not in GPU_KEYS}
        held: set[tuple[str, int]] = set()
        for lease in self._leases.values():
            on_host = (lease.host == host) if host is not None else not self._stranded(lease)
            for k, v in lease.amounts.items():
                if k in GPU_KEYS or (not on_host and k not in PLATFORM_KEYS):
                    continue
                out[k] = out.get(k, 0.0) + v
            if on_host:
                held.update((lease.host, d) for d in lease.gpu_devices)
        if held or GPU_KEY in self._keys_ever_leased:
            out[GPU_KEY] = float(len(held))
        return out

    def available(self, host: str | None = None) -> dict[str, float]:
        if host is None:
            used = self.used()
            return {k: cap - used.get(k, 0.0) for k, cap in self.pool.capacity.items()}
        h = self.pool.host(host)
        if h is None:
            return {}
        out = self._room(h)
        if GPU_KEY in h.capacity:
            out[GPU_KEY] = float(len(h.gpus) - len(self.device_use(h.name)))
        return out

    def can_fit(self, amounts: dict[str, float], host: str | None = None) -> bool:
        if host is None:
            # A request must fit on one grantable host; a drained or quiet host's
            # capacity is not room this can use.
            return self.fit(amounts) is not None
        h = self.pool.host(host)
        if h is None:
            return False
        plain = {k: v for k, v in amounts.items() if k not in GPU_KEYS}
        room = self._room(h)
        if not all(room.get(k, 0.0) >= v for k, v in plain.items()):
            return False
        if not gpu_request(amounts)[0]:
            return True
        return self.pick_gpus(h, amounts) is not None

    def device_use(self, host: str, exclude=frozenset(),
                   pending: Iterable[tuple] = ()) -> dict[int, tuple[bool, float]]:
        """{card index: (lent whole, GB lent as shares)} for the cards in use on `host`,
        treating leases in `exclude` as released and `pending` grants as made."""
        use: dict[int, list] = {}

        def lend(cards, amounts):
            _, vram = gpu_request(amounts)
            for d in cards:
                u = use.setdefault(d, [False, 0.0])
                if vram is None:
                    u[0] = True
                else:
                    u[1] += vram

        for lease in self._leases.values():
            if lease.host == host and lease.sprint_id not in exclude:
                lend(lease.gpu_devices, lease.amounts)
        for p_host, p_amounts, p_cards in _normalize(pending):
            if p_host == host:
                lend(p_cards, p_amounts)
        return {d: (whole, shared) for d, (whole, shared) in use.items()}

    def pick_gpus(self, host: Host, amounts: dict[str, float], pending: Iterable[tuple] = (),
                  exclude=frozenset(), prefer: Iterable[int] = ()) -> list[int] | None:
        """Card indices on `host` for a request: [] when it asks for no GPU, None when
        the cards it needs are not free. Whole cards take the lowest free indices;
        shares take the cards with the least spare VRAM that still fits, keeping big
        cards whole for big work.

        `prefer` names cards a live job is already using (see Ledger.acquire): when
        enough of them, in the order given, could take this request right now, they
        are kept instead of normal placement — so a re-grant after a dispatcher
        outage doesn't hand a second job the card a running one is already on."""
        cards, vram = gpu_request(amounts)
        if cards == 0:
            return []
        use = self.device_use(host.name, exclude, pending)
        by_index = {g.index: g for g in host.gpus}
        preferred: list[int] = []
        for idx in prefer:
            g = by_index.get(idx)
            if g is None:
                continue
            whole, shared = use.get(idx, (False, 0.0))
            if vram is None:
                if idx not in use:
                    preferred.append(idx)
            else:
                if g.vram_gb is not None and not whole and g.vram_gb - shared >= vram:
                    preferred.append(idx)
        if len(preferred) >= cards:
            return sorted(preferred[:cards])
        if vram is None:
            free = [g.index for g in host.gpus if g.index not in use]
            return free[:cards] if len(free) >= cards else None
        fits: list[tuple[float, int]] = []
        for g in host.gpus:
            whole, shared = use.get(g.index, (False, 0.0))
            if g.vram_gb is None or whole:
                continue
            if g.vram_gb - shared >= vram:
                fits.append((g.vram_gb - shared, g.index))
        if len(fits) < cards:
            return None
        return sorted(index for _, index in sorted(fits)[:cards])

    def _room(self, host: Host, exclude=frozenset()) -> dict[str, float]:
        """Free amounts on `host` for everything but GPUs; platform keys pool-wide."""
        used: dict[str, float] = {}
        for lease in self._leases.values():
            if lease.sprint_id in exclude:
                continue
            for k, v in lease.amounts.items():
                if k not in GPU_KEYS and (k in PLATFORM_KEYS or lease.host == host.name):
                    used[k] = used.get(k, 0.0) + v
        out = {k: cap - used.get(k, 0.0) for k, cap in self.pool.capacity.items()
               if k in PLATFORM_KEYS}
        out.update({k: cap - used.get(k, 0.0) for k, cap in host.capacity.items()
                    if k not in GPU_KEYS})
        return out

    def fit(self, amounts: dict[str, float], program: str | None = None,
            pending: Iterable[tuple] = (), exclude=frozenset(),
            host: str | None = None, prefer: Iterable[int] = (),
            readopt: bool = False) -> tuple[str, list[int]] | None:
        """(host, card indices) on the first placeable host `program` may use that holds
        ALL of `amounts`, or None. `pending` is what this cycle has granted but not yet
        acquired, so one pass of grants never books the same room twice; `exclude`
        names leases to treat as released, for weighing preemption; `host` limits the
        search to one host; `prefer` is passed to `pick_gpus`. `readopt` re-adopts a
        sprint whose agent or job is still physically running: a drained or quiet
        host still holds its room — only NEW grants are kept off such a host."""
        pending = _normalize(pending)
        if amounts and all(k in PLATFORM_KEYS for k in amounts):
            # A request made only of platform-wide keys (a worker or housekeeper slot)
            # is the platform's own bookkeeping, not a program's work: it consumes
            # pool-wide capacity that no host owns, so per-program server access must
            # not gate it. Once every server carried an explicit `programs:` list, this
            # went through `grantable_hosts(None)` — which no server admits — so the PM
            # and wiki loops could not take a slot and idled silently (2026-09-18).
            hosts = [h for h in self.pool.hosts if h.name == LOCAL] or list(self.pool.hosts)
        else:
            hosts = (self.pool.placeable_hosts(program) if readopt
                     else self.pool.grantable_hosts(program))
        for h in hosts:
            if host is not None and h.name != host:
                continue
            room = self._room(h, exclude)
            for p_host, p_amounts, _ in pending:
                for k, v in p_amounts.items():
                    if k not in GPU_KEYS and (k in PLATFORM_KEYS or p_host == h.name):
                        room[k] = room.get(k, 0.0) - v
            if not all(room.get(k, 0.0) >= v for k, v in amounts.items() if k not in GPU_KEYS):
                continue
            cards = self.pick_gpus(h, amounts, pending, exclude, prefer)
            if cards is not None:
                return h.name, cards
        return None

    def fit_host(self, amounts: dict[str, float], program: str | None = None,
                 pending: Iterable[tuple] = ()) -> str | None:
        """The host `fit` would choose, or None."""
        placed = self.fit(amounts, program, pending)
        return placed[0] if placed else None

    # --- mutations ---
    def acquire(self, sprint_id, amounts, now, ttl, priority=0, preemptible=True,
                program=None, prefer_cards=(), host=None, readopt=False):
        existing = self._leases.get(sprint_id)
        if existing is not None:
            return existing
        placed = self.fit(amounts, program, prefer=prefer_cards, host=host, readopt=readopt)
        if placed is None:
            return None
        host, cards = placed
        lease = Lease(
            id=uuid.uuid4().hex[:12],
            sprint_id=sprint_id,
            amounts={str(k): float(v) for k, v in amounts.items()},
            granted_at=float(now),
            expires_at=float(now) + float(ttl),
            priority=int(priority),
            preemptible=bool(preemptible),
            host=host,
            gpu_devices=cards,
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
