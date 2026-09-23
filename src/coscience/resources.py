"""Declared resource capacity for an environment, as a list of hosts."""
from __future__ import annotations

import contextlib
import fcntl
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

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

# Both keys are popped off a spec while parsing: `programs` is the one shape that
# still exists, `exclude_programs` is only checked for so a hand-edited file using
# the old shape gets a clear error instead of being read as a capacity amount.
_ACCESS_KEYS = ("programs", "exclude_programs")


@dataclass
class Gpu:
    index: int
    vram_gb: float | None = None     # None: never declared; such a card is only lent whole
    model: str = ""
    # What the card has, as probed or typed in (G2); `vram_gb` is what Co-Science may use
    # of it. None when nobody recorded it.
    total_vram_gb: float | None = None
    # Switched off (G2): kept on file so it can be switched back on, but lent to no one.
    # Its index stays its physical position, so the cards after it keep their numbers.
    # Written `disabled: true` — never `off:`, which YAML 1.1 reads as the boolean false.
    disabled: bool = False


@dataclass
class Host:
    name: str
    capacity: dict[str, float] = field(default_factory=dict)
    ssh: str = ""                                        # "" = the dispatcher's own machine
    programs: list[str] | None = None                    # None = every program; else exactly these
    run_root: str = ""                                   # where sprint work goes on the host
    gpus: list[Gpu] = field(default_factory=list)
    shared: bool = False                                 # other people use this machine too
    owner: str = ""                                      # who to ask about it
    notes: str = ""                                      # usage rules, e.g. hours or longest job
    label: str = ""                                      # what a human calls it; "" = go by the name
    drain: bool = False                                  # takes no new grants; running work finishes
    drained_at: float = 0.0                              # time.time() when drain was set; 0.0 = unknown/long ago
    removing: bool = False                               # marked for removal; the dispatcher deletes it once empty
    # The machine's own totals (G2): what the probe found or a human typed in. The
    # capacity above is what Co-Science may use of it, never more.
    machine: dict[str, float] = field(default_factory=dict)
    cards_off: list[Gpu] = field(default_factory=list)   # switched-off cards, for display

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
        if self.programs is None:
            return True
        return program is not None and program in self.programs


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
        """Hosts a new grant may land on: placeable, allowed, not drained, not
        marked for removal, not closed."""
        return [h for h in self.placeable_hosts(program)
                if not h.drain and not h.removing and h.name not in self.closed]

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
        # Popped before `flat`, which reads every remaining top-level key as a
        # number: a label left in there would abort the whole pool file.
        local_label = raw.pop("label", None)
        if local_label is None and raw is not d:
            local_label = d.get("label")
        machine_spec = raw.pop("machine", None)
        if machine_spec is None and raw is not d:
            machine_spec = d.get("machine")
        access_specs: dict[str, object] = {}
        for key in _ACCESS_KEYS:
            val = raw.pop(key, None)
            if val is None and raw is not d:
                val = d.get(key)
            if val is not None:
                access_specs[key] = val
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
        local_off: list[Gpu] = []
        if gpu_specs is not None:
            try:
                all_cards = _parse_gpus("", gpu_specs)
            except ValueError as exc:
                host_errors.append(str(exc))
            else:
                local_gpus = [g for g in all_cards if not g.disabled]
                local_off = [g for g in all_cards if g.disabled]
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
        local_programs: list[str] | None = None
        if access_specs:
            try:
                local_programs = _parse_programs("", access_specs)
            except ValueError as exc:
                host_errors.append(str(exc))       # reported on Compute; local stays open
        local_machine: dict[str, float] = {}
        if machine_spec is not None:
            try:
                local_machine = _parse_machine("", machine_spec, local_capacity)
            except ValueError as exc:
                host_errors.append(str(exc))       # reported on Compute; the capacity stands
        hosts = [Host(LOCAL, local_capacity, gpus=local_gpus, programs=local_programs,
                      label=str(local_label or "").strip(), machine=local_machine,
                      cards_off=local_off)]
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
    if "remove" in spec and not isinstance(spec["remove"], bool):
        raise ValueError(f"hosts.{name}.remove: must be true or false")
    programs = _parse_programs(f"hosts.{name}.", spec)
    gpus: list[Gpu] = []
    cards_off: list[Gpu] = []
    if spec.get("gpus") is not None:
        all_cards = _parse_gpus(f"hosts.{name}.", spec["gpus"])
        gpus = [g for g in all_cards if not g.disabled]
        cards_off = [g for g in all_cards if g.disabled]
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
                programs=programs,
                run_root=str(spec.get("run_root") or ""), gpus=gpus,
                shared=bool(spec.get("shared", False)), owner=str(spec.get("owner") or ""),
                notes=str(spec.get("notes") or ""), label=str(spec.get("label") or "").strip(),
                drain=bool(spec.get("drain", False)),
                drained_at=drained_at, removing=bool(spec.get("remove", False)),
                machine=_parse_machine(f"hosts.{name}.", spec.get("machine"), capacity),
                cards_off=cards_off)


MACHINE_KEYS = ("cpu", "memory_gb")


def _parse_machine(where: str, spec, capacity: dict[str, float]) -> dict[str, float]:
    """A machine's own totals (G2): `machine: {cpu: 32, memory_gb: 62}`. What Co-Science
    may use (`capacity`) can be less, never more — a declaration above the machine is a
    typo, and planning around it would over-commit the machine."""
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise ValueError(f"{where}machine: must be a mapping like {{cpu: 32, memory_gb: 62}}")
    out: dict[str, float] = {}
    for key, val in spec.items():
        key = str(key)
        if key not in MACHINE_KEYS:
            raise ValueError(f"{where}machine.{key}: only {', '.join(MACHINE_KEYS)} are recorded")
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or not math.isfinite(val) or val <= 0):
            raise ValueError(f"{where}machine.{key}: must be a positive number")
        out[key] = float(val)
        if capacity.get(key, 0.0) > out[key]:
            raise ValueError(f"{where}{key}: {capacity[key]:g} available is more than the "
                             f"machine's {out[key]:g}")
    return out


def _parse_programs(where: str, spec: dict) -> list[str] | None:
    """A server's program list: `programs:` names exactly the programs it runs;
    an absent key admits every program. `where` prefixes messages, e.g. "hosts.a.".
    `exclude_programs` is a removed shape, rejected outright rather than read as
    a capacity amount or silently ignored."""
    if spec.get("exclude_programs") is not None:
        raise ValueError(f"{where}exclude_programs is no longer supported; "
                         "list the programs the server runs under programs:")
    raw = spec.get("programs")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"{where}programs: must be a list of program ids")
    return [str(p) for p in raw]


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
        total = card.get("total_vram_gb")
        if total is not None:
            if (isinstance(total, bool) or not isinstance(total, (int, float))
                    or not math.isfinite(total) or total <= 0):
                raise ValueError(f"{where}gpus[{i}].total_vram_gb: must be a positive number")
            if vram > total:
                raise ValueError(f"{where}gpus[{i}]: {vram:g} GB available is more than the "
                                 f"card's {total:g} GB")
        disabled = card.get("disabled", False)
        if not isinstance(disabled, bool):
            raise ValueError(f"{where}gpus[{i}].disabled: must be true or false")
        cards.append(Gpu(index=i, vram_gb=float(vram), model=str(card.get("model") or ""),
                         total_vram_gb=float(total) if total is not None else None,
                         disabled=disabled))
    return cards


def load_pool(repo_root) -> ResourcePool:
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    if not path.is_file():
        return ResourcePool()
    return ResourcePool.from_yaml(path)


@contextlib.contextmanager
def pool_file_lock(repo_root):
    """Repo-level exclusive flock around a read-modify-write of resources.yaml.

    Every writer of the pool file — the service's capacity and host-admin edits,
    and the dispatcher's marked-server removal — holds this, so two processes
    (or two requests in the HTTP server's threadpool) never race the same
    read-modify-write and clobber each other's edit. Same shape as
    `housekeeping._guard`."""
    lockdir = Path(repo_root) / ".coscience"
    lockdir.mkdir(parents=True, exist_ok=True)
    with open(lockdir / "resources.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_pool_file(repo_root, data: dict) -> None:
    """Atomically write resources.yaml. Callers should hold `pool_file_lock` for
    the whole read-modify-write this belongs to."""
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per call: some callers are sync HTTP routes, which FastAPI runs in a
    # threadpool, so concurrent calls are genuinely concurrent. A shared tmp name
    # lets one thread's os.replace pull the file out from under another thread's
    # write/replace.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(yaml.safe_dump(data, sort_keys=True))
        os.replace(tmp, path)  # atomic: a reader never sees a partial file
    finally:
        tmp.unlink(missing_ok=True)


def pool_file_hosts(repo_root) -> tuple[dict, dict]:
    """Load resources.yaml and return (the loaded document, its `hosts:` mapping) —
    unwrapping a `resources:` wrapper when that's where `hosts:` lives. The returned
    mapping is already installed as `loaded`'s (or its wrapper's) "hosts" key, so
    mutating it in place and passing `loaded` to `write_pool_file` keeps the edit."""
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    try:
        loaded = yaml.safe_load(path.read_text()) if path.is_file() else {}
    except yaml.YAMLError as exc:
        raise ValueError(f"resources.yaml could not be read: {exc}")
    loaded = loaded if isinstance(loaded, dict) else {}
    wrapped = loaded.get("resources")
    holder = wrapped if isinstance(wrapped, dict) and "hosts" in wrapped else loaded
    if "hosts" in holder and not isinstance(holder["hosts"], dict):
        raise ValueError("resources.yaml hosts: is not a mapping; fix the file first")
    hosts = holder.get("hosts") or {}
    holder["hosts"] = hosts
    return loaded, hosts


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
