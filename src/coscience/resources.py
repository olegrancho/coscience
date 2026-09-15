"""Declared resource capacity for an environment, as a list of hosts."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from coscience.host_probe import _TARGET as _SSH_TARGET

WORKER_KEY = "workers"
# Bound how many agent processes run at once on the dispatcher's machine, wherever
# their work is placed — so they are counted across the pool, never per host.
PLATFORM_KEYS = frozenset({WORKER_KEY, "housekeepers"})
LOCAL = "local"
# Both processes read this: the HTTP server (so the dashboard's placement view and
# any admin action agree with what will actually be scheduled) and the dispatch
# loop (which is what actually grants leases on remote hosts). Set it for both or
# the two will disagree about which hosts are placeable.
REMOTE_ENV = "COSCIENCE_ALLOW_REMOTE"

GPU_KEY = "gpu"
GPU_VRAM_KEY = "gpu_vram_gb"
# Allocated per card on one host, never summed like cpu: `gpu` is how many cards a
# request needs, and `gpu_vram_gb`, when present, the VRAM it needs on each of them.
GPU_KEYS = frozenset({GPU_KEY, GPU_VRAM_KEY})


@dataclass
class Gpu:
    index: int
    vram_gb: float | None = None     # None: never declared; such a card is only lent whole
    model: str = ""


@dataclass
class Host:
    name: str
    capacity: dict[str, float] = field(default_factory=dict)
    ssh: str = ""                                        # "" = the dispatcher's own machine
    programs: list[str] = field(default_factory=list)   # [] = every program
    run_root: str = ""                                   # where sprint work goes on the host
    gpus: list[Gpu] = field(default_factory=list)
    shared: bool = False                                 # other people use this machine too
    owner: str = ""                                      # who to ask about it
    notes: str = ""                                      # usage rules, e.g. hours or longest job
    drain: bool = False                                  # takes no new grants; running work finishes
    drained_at: float = 0.0                              # time.time() when drain was set; 0.0 = unknown/long ago

    def __post_init__(self):
        if not self.gpus and self.capacity.get(GPU_KEY, 0.0) >= 1:
            # A bare `gpu: N` count: N cards whose VRAM nobody declared. They are lent
            # whole, exactly as a GPU count always was, and never shared.
            self.gpus = [Gpu(i) for i in range(int(self.capacity[GPU_KEY]))]

    @property
    def is_local(self) -> bool:
        return not self.ssh

    @property
    def placeable(self) -> bool:
        # A remote host takes work only where the deployment turned remote placement
        # on: it sends sprint code and data to another machine.
        return self.is_local or os.environ.get(REMOTE_ENV) == "1"

    def allows(self, program: str | None) -> bool:
        return not self.programs or (program is not None and program in self.programs)


@dataclass
class ResourcePool:
    # Pool-wide totals: platform keys plus the capacity of every placeable host.
    capacity: dict[str, float] = field(default_factory=dict)
    hosts: list[Host] | None = None
    # Human-readable messages for `hosts:` entries that were skipped, one per bad
    # entry — parsing never fails the whole pool over one hand-edited typo.
    host_errors: list[str] = field(default_factory=list)
    # Hosts that take no new grants this cycle and why (e.g. quiet); set by the
    # dispatcher, never parsed.
    closed: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.hosts is None:
            # Built from a flat map: every non-platform amount belongs to this machine.
            self.hosts = [Host(LOCAL, {k: v for k, v in self.capacity.items()
                                       if k not in PLATFORM_KEYS})]

    def host(self, name: str) -> Host | None:
        return next((h for h in self.hosts if h.name == name), None)

    def placeable_hosts(self, program: str | None) -> list[Host]:
        return [h for h in self.hosts if h.placeable and h.allows(program)]

    def grantable_hosts(self, program: str | None) -> list[Host]:
        """Hosts a new grant may land on: placeable, allowed, not drained, not closed."""
        return [h for h in self.placeable_hosts(program) if not h.drain and h.name not in self.closed]

    @classmethod
    def from_dict(cls, d: dict) -> "ResourcePool":
        if not isinstance(d, dict):
            return cls()
        raw = d.get("resources", d)
        raw = dict(raw) if isinstance(raw, dict) else {}
        host_specs = raw.pop("hosts", None)
        if host_specs is None:
            host_specs = d.get("hosts")          # beside a `resources:` wrapper
        gpu_specs = raw.pop("gpus", None)
        if gpu_specs is None and raw is not d:
            gpu_specs = d.get("gpus")
        host_specs = host_specs or {}
        host_errors: list[str] = []
        if not isinstance(host_specs, dict):
            host_errors.append("hosts: must be a mapping of host name to host")
            host_specs = {}

        flat = {str(k): float(v) for k, v in raw.items()}
        local_capacity = {k: v for k, v in flat.items() if k not in PLATFORM_KEYS}
        if GPU_VRAM_KEY in local_capacity:
            del local_capacity[GPU_VRAM_KEY]
            host_errors.append(
                "gpu_vram_gb: is a request key, not capacity; declare cards under gpus:")
        local_gpus: list[Gpu] = []
        if gpu_specs is not None:
            try:
                local_gpus = _parse_gpus("", gpu_specs)
            except ValueError as exc:
                host_errors.append(str(exc))
            else:
                if GPU_KEY in local_capacity and int(local_capacity[GPU_KEY]) != len(local_gpus):
                    host_errors.append(f"gpus: {len(local_gpus)} card(s) listed but gpu is "
                                       f"{local_capacity[GPU_KEY]:g}; using the list")
                local_capacity[GPU_KEY] = float(len(local_gpus))
        if not local_gpus:
            # No card list took hold (none given, or it didn't parse): a bare `gpu`
            # count must still be a whole number of cards, or Host.__post_init__
            # silently truncates it while the pool-wide gauge keeps the fraction.
            v = local_capacity.get(GPU_KEY)
            if v is not None and v != int(v):
                host_errors.append(f"gpu: {v:g} is not a whole number of cards; using {int(v)}")
                local_capacity[GPU_KEY] = float(int(v))
        hosts = [Host(LOCAL, local_capacity, gpus=local_gpus)]
        for name, spec in host_specs.items():
            try:
                hosts.append(_parse_host(str(name), spec))
            except ValueError as exc:
                host_errors.append(str(exc))

        capacity = {k: v for k, v in flat.items() if k in PLATFORM_KEYS}
        for h in hosts:
            if h.placeable:
                for k, v in h.capacity.items():
                    capacity[k] = capacity.get(k, 0.0) + v
        return cls(capacity=capacity, hosts=hosts, host_errors=host_errors)

    @classmethod
    def from_yaml(cls, path) -> "ResourcePool":
        return cls.from_dict(yaml.safe_load(Path(path).read_text()) or {})


def _parse_host(name: str, spec) -> Host:
    if name == LOCAL:
        raise ValueError("hosts: 'local' is this machine; declare its capacity at the top level")
    if not isinstance(spec, dict):
        raise ValueError(f"hosts.{name}: must be a mapping")
    ssh = str(spec.get("ssh") or "").strip()
    if not ssh:
        raise ValueError(f"hosts.{name}: needs ssh (an ssh alias or user@host)")
    if not _SSH_TARGET.match(ssh):
        # Same pattern host_probe.ssh_argv validates against — reject here so a bad
        # value (e.g. one starting with "-", which ssh reads as an option) never
        # reaches a beat and raises ValueError mid-cycle (Fix C).
        raise ValueError(f"hosts.{name}.ssh: {ssh!r} must be an alias, user@host or user@host:port")
    cap_raw = spec.get("capacity") or {}
    if not isinstance(cap_raw, dict):
        raise ValueError(f"hosts.{name}: capacity must be a mapping")
    capacity: dict[str, float] = {}
    for key, val in cap_raw.items():
        key = str(key)
        if key in PLATFORM_KEYS:
            raise ValueError(f"hosts.{name}: {key} is platform-wide, not per host")
        if key == GPU_VRAM_KEY or key == "gpus":
            raise ValueError(f"hosts.{name}.capacity.{key}: GPU detail belongs under gpus:, not capacity")
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or not math.isfinite(val) or val < 0):
            raise ValueError(f"hosts.{name}.{key}: capacity must be a finite non-negative number")
        capacity[key] = float(val)
    if "drain" in spec and not isinstance(spec["drain"], bool):
        raise ValueError(f"hosts.{name}.drain: must be true or false")
    programs = spec.get("programs") or []
    if not isinstance(programs, list):
        raise ValueError(f"hosts.{name}: programs must be a list")
    gpus: list[Gpu] = []
    if spec.get("gpus") is not None:
        gpus = _parse_gpus(f"hosts.{name}.", spec["gpus"])
        if GPU_KEY in capacity and int(capacity[GPU_KEY]) != len(gpus):
            raise ValueError(f"hosts.{name}: gpus lists {len(gpus)} card(s) but "
                             f"capacity.gpu is {capacity[GPU_KEY]:g}")
        capacity[GPU_KEY] = float(len(gpus))
    elif GPU_KEY in capacity and capacity[GPU_KEY] != int(capacity[GPU_KEY]):
        raise ValueError(f"hosts.{name}.capacity.gpu: must be a whole number of cards")
    drained_at_raw = spec.get("drained_at")
    drained_at = (float(drained_at_raw)
                  if isinstance(drained_at_raw, (int, float)) and not isinstance(drained_at_raw, bool)
                  else 0.0)
    return Host(name=name, capacity=capacity, ssh=ssh,
                programs=[str(p) for p in programs],
                run_root=str(spec.get("run_root") or ""), gpus=gpus,
                shared=bool(spec.get("shared", False)), owner=str(spec.get("owner") or ""),
                notes=str(spec.get("notes") or ""), drain=bool(spec.get("drain", False)),
                drained_at=drained_at)


def _parse_gpus(where: str, spec) -> list[Gpu]:
    """Cards from a `gpus:` list. `where` prefixes messages ("" for this machine,
    "hosts.<name>." for a remote host)."""
    if not isinstance(spec, list):
        raise ValueError(f"{where}gpus: must be a list of cards")
    cards: list[Gpu] = []
    for i, card in enumerate(spec):
        if not isinstance(card, dict):
            raise ValueError(f"{where}gpus[{i}]: must be a mapping with vram_gb")
        vram = card.get("vram_gb")
        if (isinstance(vram, bool) or not isinstance(vram, (int, float))
                or not math.isfinite(vram) or vram <= 0):
            raise ValueError(f"{where}gpus[{i}].vram_gb: must be a positive number")
        cards.append(Gpu(index=i, vram_gb=float(vram), model=str(card.get("model") or "")))
    return cards


def load_pool(repo_root) -> ResourcePool:
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    if not path.is_file():
        return ResourcePool()
    return ResourcePool.from_yaml(path)


def gpu_request(required: dict[str, float]) -> tuple[int, float | None]:
    """(cards, VRAM GB on each) a request asks for; (0, None) when it asks for no GPU.
    `gpu` alone asks for whole cards; `gpu_vram_gb` makes them shares that other work
    may use too, and on its own means one card."""
    required = required or {}
    vram = float(required.get(GPU_VRAM_KEY) or 0.0)
    count = float(required.get(GPU_KEY) or 0.0)
    if vram > 0 and count <= 0:
        count = 1.0
    cards = math.ceil(count) if count > 0 else 0
    return cards, (vram if vram > 0 else None)


def _gpu_shortfall(required: dict[str, float], host: Host) -> dict[str, tuple[float, float]]:
    """What a host can never give a request's GPU part: too few cards, or no card
    with enough declared VRAM."""
    cards, vram = gpu_request(required)
    if cards == 0:
        return {}
    if cards > len(host.gpus):
        return {GPU_KEY: (float(cards), float(len(host.gpus)))}
    if vram is None:
        return {}
    declared = [g.vram_gb for g in host.gpus if g.vram_gb is not None]
    if sum(1 for v in declared if v >= vram) >= cards:
        return {}
    return {GPU_VRAM_KEY: (vram, max(declared, default=0.0))}


def over_capacity_on(required: dict[str, float], pool: ResourcePool, program: str | None = None,
                     only_host: str | None = None) -> tuple[str | None, dict[str, tuple[float, float]]]:
    """(closest host name, {resource: (requested, capacity)}) for a request no allowed
    host can ever hold. Such a sprint is never granted however long it waits. Host
    amounts must fit on ONE host — 16 cpu across two machines is not 16 on one — so
    when none fits, the host missing the fewest resources (and, tied on that, the
    smallest shortfall) is the one reported, alongside its name. Platform keys are
    compared against the pool. `only_host`, when given, restricts consideration to
    that one host (a sprint pinned there by earlier work) — the name it returns is
    then `only_host` itself, or None when that host isn't in the placeable pool."""
    required = {k: float(v) for k, v in (required or {}).items()}
    over = {k: (v, pool.capacity.get(k, 0.0)) for k, v in required.items()
            if k in PLATFORM_KEYS and v > pool.capacity.get(k, 0.0)}
    on_host = {k: v for k, v in required.items()
               if k not in PLATFORM_KEYS and k not in GPU_KEYS}
    candidates = pool.placeable_hosts(program)
    if only_host is not None:
        candidates = [h for h in candidates if h.name == only_host]
    best: dict[str, tuple[float, float]] | None = None
    best_score: tuple[int, float] | None = None
    best_name: str | None = None
    for h in candidates:
        miss = {k: (v, h.capacity.get(k, 0.0)) for k, v in on_host.items()
                if v > h.capacity.get(k, 0.0)}
        miss.update(_gpu_shortfall(required, h))
        score = (len(miss), sum(v - cap for v, cap in miss.values()))
        if best is None or score < best_score:
            best, best_score, best_name = miss, score, h.name
    if best is None:
        best = {k: (v, 0.0) for k, v in on_host.items()}
        best.update(_gpu_shortfall(required, Host(LOCAL)))
    return best_name, {**over, **best}


def over_capacity(required: dict[str, float], pool: ResourcePool, program: str | None = None,
                  only_host: str | None = None) -> dict[str, tuple[float, float]]:
    """{resource: (requested, capacity)} for a request no allowed host can ever hold."""
    return over_capacity_on(required, pool, program, only_host)[1]


def describe_over_capacity(over: dict[str, tuple[float, float]]) -> str:
    """"needs cpu 24 but capacity is 16" — empty when nothing is over."""
    return "; ".join(f"needs {k} {need:g} but capacity is {cap:g}"
                     for k, (need, cap) in sorted(over.items()))


def effective_requirement(required: dict[str, float], pool: ResourcePool) -> dict[str, float]:
    """What a sprint actually consumes. When the pool declares a worker cap, every
    sprint costs one worker slot on top of what it declares — that is what bounds
    the number of agent processes running at once. A pool with no `workers` key is
    uncapped, exactly as before."""
    if WORKER_KEY not in pool.capacity:
        return dict(required)
    return {**required, WORKER_KEY: 1.0}
