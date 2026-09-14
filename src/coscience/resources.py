"""Declared resource capacity for an environment, as a list of hosts."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WORKER_KEY = "workers"
# Bound how many agent processes run at once on the dispatcher's machine, wherever
# their work is placed — so they are counted across the pool, never per host.
PLATFORM_KEYS = frozenset({WORKER_KEY, "housekeepers"})
LOCAL = "local"


@dataclass
class Host:
    name: str
    capacity: dict[str, float] = field(default_factory=dict)
    ssh: str = ""                                        # "" = the dispatcher's own machine
    programs: list[str] = field(default_factory=list)   # [] = every program
    run_root: str = ""                                   # where sprint work goes on the host

    @property
    def is_local(self) -> bool:
        return not self.ssh

    @property
    def placeable(self) -> bool:
        # Nothing can launch on a remote host until O6, so only this machine takes work.
        return self.is_local

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

    def __post_init__(self):
        if self.hosts is None:
            # Built from a flat map: every non-platform amount belongs to this machine.
            self.hosts = [Host(LOCAL, {k: v for k, v in self.capacity.items()
                                       if k not in PLATFORM_KEYS})]

    def host(self, name: str) -> Host | None:
        return next((h for h in self.hosts if h.name == name), None)

    def placeable_hosts(self, program: str | None) -> list[Host]:
        return [h for h in self.hosts if h.placeable and h.allows(program)]

    @classmethod
    def from_dict(cls, d: dict) -> "ResourcePool":
        if not isinstance(d, dict):
            return cls()
        raw = d.get("resources", d)
        raw = dict(raw) if isinstance(raw, dict) else {}
        host_specs = raw.pop("hosts", None)
        if host_specs is None:
            host_specs = d.get("hosts")          # beside a `resources:` wrapper
        host_specs = host_specs or {}
        host_errors: list[str] = []
        if not isinstance(host_specs, dict):
            host_errors.append("hosts: must be a mapping of host name to host")
            host_specs = {}

        flat = {str(k): float(v) for k, v in raw.items()}
        hosts = [Host(LOCAL, {k: v for k, v in flat.items() if k not in PLATFORM_KEYS})]
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
    cap_raw = spec.get("capacity") or {}
    if not isinstance(cap_raw, dict):
        raise ValueError(f"hosts.{name}: capacity must be a mapping")
    capacity: dict[str, float] = {}
    for key, val in cap_raw.items():
        key = str(key)
        if key in PLATFORM_KEYS:
            raise ValueError(f"hosts.{name}: {key} is platform-wide, not per host")
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or not math.isfinite(val) or val < 0):
            raise ValueError(f"hosts.{name}.{key}: capacity must be a finite non-negative number")
        capacity[key] = float(val)
    programs = spec.get("programs") or []
    if not isinstance(programs, list):
        raise ValueError(f"hosts.{name}: programs must be a list")
    return Host(name=name, capacity=capacity, ssh=ssh,
                programs=[str(p) for p in programs],
                run_root=str(spec.get("run_root") or ""))


def load_pool(repo_root) -> ResourcePool:
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    if not path.is_file():
        return ResourcePool()
    return ResourcePool.from_yaml(path)


def over_capacity(required: dict[str, float], pool: ResourcePool,
                  program: str | None = None) -> dict[str, tuple[float, float]]:
    """{resource: (requested, capacity)} for a request no allowed host can ever hold.
    Such a sprint is never granted however long it waits. Host amounts must fit on
    ONE host — 16 cpu across two machines is not 16 on one — so when none fits, the
    host missing the fewest resources (and, tied on that, the smallest shortfall) is
    the one reported. Platform keys are compared against the pool."""
    required = {k: float(v) for k, v in (required or {}).items()}
    over = {k: (v, pool.capacity.get(k, 0.0)) for k, v in required.items()
            if k in PLATFORM_KEYS and v > pool.capacity.get(k, 0.0)}
    on_host = {k: v for k, v in required.items() if k not in PLATFORM_KEYS}
    best: dict[str, tuple[float, float]] | None = None
    best_score: tuple[int, float] | None = None
    for h in pool.placeable_hosts(program):
        miss = {k: (v, h.capacity.get(k, 0.0)) for k, v in on_host.items()
                if v > h.capacity.get(k, 0.0)}
        score = (len(miss), sum(v - cap for v, cap in miss.values()))
        if best is None or score < best_score:
            best, best_score = miss, score
    if best is None:
        best = {k: (v, 0.0) for k, v in on_host.items()}
    return {**over, **best}


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
