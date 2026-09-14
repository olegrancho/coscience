# O2 — Compute as Hosts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the flat resource pool into a list of hosts — each with capacity, allowed programs and a run root — so every lease names the host it is on, and no grant spans two machines or lands on a host reserved for another program.

**Architecture:** `.coscience/resources.yaml` keeps its top-level amounts, which now mean the `local` host (plus the platform-wide `workers` and `housekeepers`), and gains an optional `hosts:` section for remote machines. `ResourcePool` carries `hosts`; `Ledger` accounts per host, with platform keys still pool-wide; the scheduler and dispatcher place each grant on the first allowed host that holds the whole request. Remote hosts are parsed, validated and shown, but are **not placeable** until O6 builds remote launch — so on a real install nothing changes behaviour except that leases gain `host: local`.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, pytest. No frontend change in O2 (the per-host view is O7).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§4 Hosts, §10 row O2)

## Global Constraints

- Never commit or push without Oleg's explicit approval — the commit steps below run only once he has said so.
- Runtime is Linux-only. Run Python through `~/venvs/coscience/bin/python` (a uv venv; no `pip`).
- Deploy only on request.
- Platform keys are exactly `workers` and `housekeepers`; they are pool-wide, never per host.
- The dispatcher's own machine is the host named `local`; `local` cannot appear under `hosts:`.
- A host with `ssh` set is remote; remote hosts are not placeable in O2.
- Existing flat `resources.yaml` files, `resources:` wrappers, and `leases.json` files without `host` must keep loading unchanged.
- Access is SSH key only: a host entry never holds a secret.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/resources.py` | modify | `Host`, `PLATFORM_KEYS`, `LOCAL`; `ResourcePool.hosts` and host lookups; parsing and validation of `hosts:`; host-aware `over_capacity` |
| `src/coscience/models.py` | modify | `Lease.host` |
| `src/coscience/ledger.py` | modify | per-host `used` / `available` / `can_fit`; `fit_host`; `acquire(program=)` records the host |
| `src/coscience/scheduler.py` | modify | grants and yield victims simulated per host |
| `src/coscience/dispatcher.py` | modify | passes `sprint.program` to acquire and `over_capacity` |
| `src/coscience/service.py` | modify | `unrunnable` per program; `ledger_status` exposes hosts and lease host; `set_capacity` preserves `hosts:` |
| `src/coscience/pm_agent.py` | modify | COMPUTE block counts only the hosts the program may use |
| `tests/conftest.py` | modify | `every_host_placeable` fixture |
| `tests/test_resources_hosts.py` | create | parsing, validation, reservation, `over_capacity` |
| `tests/test_ledger_hosts.py` | create | per-host accounting |
| `tests/test_scheduler_hosts.py` | create | per-host grants and victims |
| `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` | modified already | §4 names the file this plan uses |

**Known follow-up, not O2:** the Compute page's capacity editor edits `ledger_status()["capacity"]`, which is the placeable total. While only `local` is placeable that equals the top-level amounts. When O6 makes remote hosts placeable, the editor must edit the `local` host instead of the total — O6/O7 own that change.

---

### Task 1: Hosts in the resource pool

**Files:**
- Modify: `src/coscience/resources.py` (whole file; currently 55 lines)
- Modify: `tests/conftest.py` (append a fixture)
- Test: `tests/test_resources_hosts.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `PLATFORM_KEYS: frozenset[str]` = `{"workers", "housekeepers"}`
  - `LOCAL: str` = `"local"`
  - `Host(name: str, capacity: dict[str, float] = {}, ssh: str = "", programs: list[str] = [], run_root: str = "")` with properties `is_local: bool`, `placeable: bool`, and method `allows(program: str | None) -> bool`
  - `ResourcePool(capacity: dict[str, float] = {}, hosts: list[Host] | None = None)`; `pool.host(name) -> Host | None`; `pool.placeable_hosts(program: str | None) -> list[Host]`
  - `over_capacity(required, pool, program: str | None = None) -> dict[str, tuple[float, float]]`
  - fixture `every_host_placeable` (monkeypatches `Host.placeable` to `True`)

- [ ] **Step 1: Add the shared fixture to `tests/conftest.py`**

Append at the end of the file:

```python
@pytest.fixture
def every_host_placeable(monkeypatch):
    """Remote launch is O6; until then only `local` is placeable. The accounting is
    host-aware already, so tests exercise it as if remote hosts were live."""
    from coscience.resources import Host
    monkeypatch.setattr(Host, "placeable", property(lambda self: True))
```

(`pytest` is already imported in `tests/conftest.py`; if it is not, add `import pytest` at the top.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_resources_hosts.py`:

```python
"""O2: the pool is a list of hosts; top-level amounts are the local host."""
import pytest

from coscience.resources import LOCAL, ResourcePool, over_capacity

FILE = {
    "cpu": 24, "gpu": 1, "workers": 3, "housekeepers": 2,
    "hosts": {
        "remote1": {"ssh": "remote1", "run_root": "~/coscience-runs", "programs": ["p2"],
                 "capacity": {"cpu": 28, "memory_gb": 28}},
    },
}


def test_top_level_amounts_are_the_local_host_and_platform_keys_stay_pool_wide():
    pool = ResourcePool.from_dict(FILE)
    local = pool.host(LOCAL)
    assert local.capacity == {"cpu": 24.0, "gpu": 1.0}
    assert local.is_local and local.placeable and local.allows("p5")
    assert pool.capacity == {"cpu": 24.0, "gpu": 1.0, "workers": 3.0, "housekeepers": 2.0}


def test_a_remote_host_is_parsed_but_not_placeable_until_remote_launch_exists():
    pool = ResourcePool.from_dict(FILE)
    remote1 = pool.host("remote1")
    assert remote1.ssh == "remote1" and remote1.run_root == "~/coscience-runs"
    assert remote1.capacity == {"cpu": 28.0, "memory_gb": 28.0}
    assert not remote1.is_local and not remote1.placeable
    assert [h.name for h in pool.placeable_hosts("p2")] == [LOCAL]


def test_a_reserved_host_admits_only_its_programs():
    remote1 = ResourcePool.from_dict(FILE).host("remote1")
    assert remote1.allows("p2")
    assert not remote1.allows("p5")
    assert not remote1.allows(None)


def test_placeable_totals_include_a_live_remote_host(every_host_placeable):
    pool = ResourcePool.from_dict(FILE)
    assert pool.capacity["cpu"] == 52.0
    assert [h.name for h in pool.placeable_hosts("p5")] == [LOCAL]
    assert [h.name for h in pool.placeable_hosts("p2")] == [LOCAL, "remote1"]


def test_a_flat_pool_is_one_local_host():
    pool = ResourcePool({"gpu": 1.0, "workers": 2.0})
    assert [h.name for h in pool.hosts] == [LOCAL]
    assert pool.host(LOCAL).capacity == {"gpu": 1.0}
    assert pool.capacity == {"gpu": 1.0, "workers": 2.0}


def test_the_legacy_resources_wrapper_still_reads():
    pool = ResourcePool.from_dict({"resources": {"gpu": 1}})
    assert pool.capacity == {"gpu": 1.0}
    assert pool.host(LOCAL).capacity == {"gpu": 1.0}


@pytest.mark.parametrize("hosts, message", [
    ({"local": {"ssh": "x"}}, "'local' is this machine"),
    ({"b": {"capacity": {"cpu": 1}}}, "needs ssh"),
    ({"b": {"ssh": "b", "capacity": {"workers": 1}}}, "platform-wide"),
    ({"b": {"ssh": "b", "capacity": {"cpu": -1}}}, "non-negative number"),
    ({"b": {"ssh": "b", "capacity": {"cpu": True}}}, "non-negative number"),
    ({"b": {"ssh": "b", "programs": "p2"}}, "programs must be a list"),
    ({"b": "remote1"}, "must be a mapping"),
])
def test_a_malformed_host_is_refused_by_name(hosts, message):
    with pytest.raises(ValueError, match=message):
        ResourcePool.from_dict({"cpu": 1, "hosts": hosts})


def test_hosts_must_be_a_mapping():
    with pytest.raises(ValueError, match="hosts: must be a mapping"):
        ResourcePool.from_dict({"cpu": 1, "hosts": ["remote1"]})


def test_over_capacity_asks_whether_any_allowed_host_holds_the_whole_request():
    pool = ResourcePool.from_dict(FILE)
    assert over_capacity({"cpu": 24.0, "workers": 1.0}, pool, "p2") == {}
    assert over_capacity({"cpu": 28.0}, pool, "p2") == {"cpu": (28.0, 24.0)}   # remote1 not placeable yet
    assert over_capacity({"workers": 4.0}, pool, "p2") == {"workers": (4.0, 3.0)}


def test_over_capacity_respects_reservation_when_remote_hosts_are_live(every_host_placeable):
    pool = ResourcePool.from_dict(FILE)
    assert over_capacity({"cpu": 28.0}, pool, "p2") == {}
    assert over_capacity({"cpu": 28.0}, pool, "p5") == {"cpu": (28.0, 24.0)}


def test_over_capacity_does_not_add_cpu_across_hosts(every_host_placeable):
    pool = ResourcePool.from_dict(FILE)
    # 52 cpu in total, but no single host has 30
    assert over_capacity({"cpu": 30.0}, pool, "p2") == {"cpu": (30.0, 28.0)}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_resources_hosts.py -q`
Expected: FAIL — `ImportError: cannot import name 'LOCAL' from 'coscience.resources'`

- [ ] **Step 4: Implement**

Replace `src/coscience/resources.py` with:

```python
"""Declared resource capacity for an environment, as a list of hosts."""
from __future__ import annotations

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
        if not isinstance(host_specs, dict):
            raise ValueError("hosts: must be a mapping of host name to host")

        flat = {str(k): float(v) for k, v in raw.items()}
        hosts = [Host(LOCAL, {k: v for k, v in flat.items() if k not in PLATFORM_KEYS})]
        hosts += [_parse_host(str(name), spec) for name, spec in host_specs.items()]

        capacity = {k: v for k, v in flat.items() if k in PLATFORM_KEYS}
        for h in hosts:
            if h.placeable:
                for k, v in h.capacity.items():
                    capacity[k] = capacity.get(k, 0.0) + v
        return cls(capacity=capacity, hosts=hosts)

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
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
            raise ValueError(f"hosts.{name}.{key}: capacity must be a non-negative number")
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
    host missing the fewest resources is the one reported. Platform keys are
    compared against the pool."""
    required = {k: float(v) for k, v in (required or {}).items()}
    over = {k: (v, pool.capacity.get(k, 0.0)) for k, v in required.items()
            if k in PLATFORM_KEYS and v > pool.capacity.get(k, 0.0)}
    on_host = {k: v for k, v in required.items() if k not in PLATFORM_KEYS}
    best: dict[str, tuple[float, float]] | None = None
    for h in pool.placeable_hosts(program):
        miss = {k: (v, h.capacity.get(k, 0.0)) for k, v in on_host.items()
                if v > h.capacity.get(k, 0.0)}
        if best is None or len(miss) < len(best):
            best = miss
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
```

- [ ] **Step 5: Run the new tests and the existing pool tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_resources_hosts.py tests/test_unrunnable.py::test_over_capacity_names_only_amounts_above_the_total tests/test_service_capacity.py -q`
Expected: PASS

- [ ] **Step 6: Commit (only with Oleg's approval)**

```bash
git add src/coscience/resources.py tests/conftest.py tests/test_resources_hosts.py
git commit -m "feat(compute): read the pool as a list of hosts (O2)"
```

---

### Task 2: Leases name their host

**Files:**
- Modify: `src/coscience/models.py:41-48` (`Lease`)
- Modify: `src/coscience/ledger.py` (queries, `acquire`, `acquire_key`)
- Test: `tests/test_ledger_hosts.py` (create)

**Interfaces:**
- Consumes: `LOCAL`, `PLATFORM_KEYS`, `ResourcePool.host`, `ResourcePool.placeable_hosts`, fixture `every_host_placeable` (Task 1).
- Produces:
  - `Lease.host: str = "local"`
  - `Ledger.used(host: str | None = None) -> dict[str, float]`
  - `Ledger.available(host: str | None = None) -> dict[str, float]`
  - `Ledger.can_fit(amounts, host: str | None = None) -> bool`
  - `Ledger.fit_host(amounts, program: str | None = None, pending: Iterable[tuple[str, dict[str, float]]] = ()) -> str | None`
  - `Ledger.acquire(sprint_id, amounts, now, ttl, priority=0, preemptible=True, program: str | None = None) -> Lease | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ledger_hosts.py`:

```python
"""O2: a lease names its host, and a grant never spans two machines."""
from coscience.ledger import Ledger
from coscience.resources import LOCAL, ResourcePool


def _ledger(tmp_path, pool):
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    return led


def _two_hosts(programs=None):
    big = {"ssh": "big", "capacity": {"cpu": 16}}
    if programs is not None:
        big["programs"] = programs
    return ResourcePool.from_dict({"cpu": 4, "workers": 2, "hosts": {"big": big}})


def test_a_lease_names_the_local_host_by_default(tmp_path):
    led = _ledger(tmp_path, ResourcePool({"gpu": 1.0}))
    assert led.acquire("sp1", {"gpu": 1.0}, now=0.0, ttl=60.0).host == LOCAL


def test_a_lease_file_written_before_hosts_loads_as_local(tmp_path):
    (tmp_path / "leases.json").write_text(
        '[{"id": "l1", "sprint_id": "sp1", "amounts": {"gpu": 1.0}, "granted_at": 0.0,'
        ' "expires_at": 1e12, "priority": 0, "preemptible": true}]')
    led = _ledger(tmp_path, ResourcePool({"gpu": 1.0}))
    assert led.lease_for("sp1").host == LOCAL
    assert led.available() == {"gpu": 0.0}


def test_a_remote_host_takes_no_lease_before_remote_launch_exists(tmp_path):
    led = _ledger(tmp_path, _two_hosts())
    assert led.acquire("large", {"cpu": 8.0}, now=0.0, ttl=60.0) is None


def test_the_first_host_that_holds_the_whole_request_is_chosen(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    assert led.acquire("small", {"cpu": 4.0}, now=0.0, ttl=60.0).host == LOCAL
    assert led.acquire("large", {"cpu": 8.0}, now=0.0, ttl=60.0).host == "big"


def test_cpu_on_two_hosts_is_not_one_pool(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    led.acquire("a", {"cpu": 12.0}, now=0.0, ttl=60.0)             # big; 4 left there
    assert led.available()["cpu"] == 8.0                             # 4 local + 4 big
    assert led.acquire("b", {"cpu": 8.0}, now=0.0, ttl=60.0) is None
    assert led.used("big") == {"cpu": 12.0}
    assert led.available(LOCAL) == {"cpu": 4.0, "workers": 2.0}


def test_a_reserved_host_is_never_granted_to_another_program(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts(programs=["p2"]))
    assert led.acquire("p5-c1", {"cpu": 8.0}, now=0.0, ttl=60.0, program="p5") is None
    assert led.acquire("p5-c2", {"cpu": 4.0}, now=0.0, ttl=60.0, program="p5").host == LOCAL
    assert led.acquire("p2-c1", {"cpu": 8.0}, now=0.0, ttl=60.0, program="p2").host == "big"


def test_platform_keys_are_counted_across_hosts(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    led.acquire("a", {"cpu": 1.0, "workers": 1.0}, now=0.0, ttl=60.0)
    led.acquire("b", {"cpu": 16.0, "workers": 1.0}, now=0.0, ttl=60.0)
    assert led.available(LOCAL)["workers"] == 0.0
    assert led.available("big")["workers"] == 0.0
    assert led.acquire("c", {"cpu": 1.0, "workers": 1.0}, now=0.0, ttl=60.0) is None


def test_fit_host_counts_grants_not_yet_written(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    assert led.fit_host({"cpu": 16.0}) == "big"
    assert led.fit_host({"cpu": 16.0}, pending=[("big", {"cpu": 16.0})]) is None
    assert led.fit_host({"workers": 1.0},
                        pending=[("big", {"workers": 1.0}), (LOCAL, {"workers": 1.0})]) is None


def test_a_reacquired_key_must_fit_on_the_leases_own_host(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 16, "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}}}})
    led = _ledger(tmp_path, pool)
    led.acquire("x", {"cpu": 16.0}, now=0.0, ttl=60.0)              # fills local
    assert led.acquire("a", {"cpu": 16.0}, now=0.0, ttl=60.0).host == "big"
    led.release_key("a", "cpu")
    assert led.acquire("b", {"cpu": 16.0}, now=0.0, ttl=60.0).host == "big"
    led.release("x")                                                 # local is free again
    assert led.acquire_key("a", "cpu", 16.0) is False                # but a's lease is on big
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_ledger_hosts.py -q`
Expected: FAIL — `AttributeError: 'Lease' object has no attribute 'host'`

- [ ] **Step 3: Add the host to `Lease`**

In `src/coscience/models.py`, change the `Lease` dataclass to:

```python
@dataclass
class Lease:
    id: str
    sprint_id: str
    amounts: dict[str, float]
    granted_at: float
    expires_at: float
    priority: int = 0
    preemptible: bool = True
    host: str = "local"              # the machine these amounts are on; platform keys are pool-wide
```

- [ ] **Step 4: Make the ledger host-aware**

In `src/coscience/ledger.py`:

Change the imports to:

```python
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from coscience.models import Lease
from coscience.resources import PLATFORM_KEYS, ResourcePool
```

Replace the `# --- queries ---` block (`all_leases` through `can_fit`) with:

```python
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
```

Replace `acquire` with:

```python
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
```

In `acquire_key`, change the fit check line from

```python
        if not self.can_fit({key: float(amount)}):
```

to

```python
        if not self.can_fit({key: float(amount)}, lease.host):
```

- [ ] **Step 5: Run the ledger tests, old and new**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_ledger_hosts.py tests/test_ledger.py tests/test_housekeeping_slots.py -q`
Expected: PASS

- [ ] **Step 6: Commit (only with Oleg's approval)**

```bash
git add src/coscience/models.py src/coscience/ledger.py tests/test_ledger_hosts.py
git commit -m "feat(compute): account leases per host (O2)"
```

---

### Task 3: The scheduler places grants and victims per host

**Files:**
- Modify: `src/coscience/scheduler.py:21-69` (`select_grants`, `select_yield_victims`)
- Test: `tests/test_scheduler_hosts.py` (create)

**Interfaces:**
- Consumes: `Ledger.fit_host`, `Ledger.available(host)`, `Lease.host`, `PLATFORM_KEYS`, `ResourcePool.placeable_hosts` (Tasks 1–2); `Sprint.program`.
- Produces: unchanged signatures `select_grants(candidates, queued_at, ledger, now) -> list[Sprint]` and `select_yield_victims(candidate, candidate_priority, ledger, yieldable_ids) -> list[Lease]`, now host-aware.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler_hosts.py`:

```python
"""O2: one cycle's grants and yields respect host boundaries and reservations."""
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None, program=None):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio, program=program)


def _ledger(tmp_path, big_programs=None):
    big = {"ssh": "big", "capacity": {"cpu": 8}}
    if big_programs is not None:
        big["programs"] = big_programs
    led = Ledger(ResourcePool.from_dict({"cpu": 8, "hosts": {"big": big}}),
                 tmp_path / "leases.json")
    led.load()
    return led


def test_one_cycle_does_not_double_book_a_host(tmp_path, every_host_placeable):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path)
    sprints = [_sprint(s, req={"cpu": 8.0}) for s in ("a", "b", "c")]
    granted = pol.select_grants(sprints, {"a": 0.0, "b": 1.0, "c": 2.0}, led, now=0.0)
    assert [s.id for s in granted] == ["a", "b"]           # one per host; c waits


def test_a_request_split_across_hosts_is_not_granted(tmp_path, every_host_placeable):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path)
    granted = pol.select_grants([_sprint("wide", req={"cpu": 12.0})], {"wide": 0.0}, led, now=0.0)
    assert granted == []


def test_a_reserved_host_takes_no_grant_for_another_program(tmp_path, every_host_placeable):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path, big_programs=["p2"])
    sprints = [_sprint("p5-a", req={"cpu": 8.0}, program="p5"),
               _sprint("p5-b", req={"cpu": 8.0}, program="p5")]
    granted = pol.select_grants(sprints, {"p5-a": 0.0, "p5-b": 1.0}, led, now=0.0)
    assert [s.id for s in granted] == ["p5-a"]


def test_victims_come_from_the_host_the_candidate_can_use(tmp_path, every_host_placeable):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, big_programs=["p2"])
    led.acquire("on-local", {"cpu": 8.0}, now=0.0, ttl=60.0, priority=0, program="p5")
    led.acquire("on-big", {"cpu": 8.0}, now=1.0, ttl=60.0, priority=0, program="p2")
    cand = _sprint("p5-hi", prio=5, req={"cpu": 8.0}, program="p5")
    # on-big was granted later, so a host-blind pick would take it first — but freeing
    # big does nothing for a p5 sprint.
    victims = pol.select_yield_victims(cand, 5, led, {"on-local", "on-big"})
    assert [v.sprint_id for v in victims] == ["on-local"]


def test_no_victims_when_the_only_usable_host_has_none_yieldable(tmp_path, every_host_placeable):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, big_programs=["p2"])
    led.acquire("on-local", {"cpu": 8.0}, now=0.0, ttl=60.0, priority=0, program="p5")
    led.acquire("on-big", {"cpu": 8.0}, now=1.0, ttl=60.0, priority=0, program="p2")
    cand = _sprint("p5-hi", prio=5, req={"cpu": 8.0}, program="p5")
    assert pol.select_yield_victims(cand, 5, led, {"on-big"}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_scheduler_hosts.py -q`
Expected: 4 FAIL, 1 PASS. The host-blind code compares against the pool total (16 cpu), so it grants `["wide"]`, grants both p5 sprints, and picks `["on-big"]` as the victim in both victim tests. `test_one_cycle_does_not_double_book_a_host` already passes (two 8-cpu grants fit the 16 total) and stays as a guard.

- [ ] **Step 3: Implement**

In `src/coscience/scheduler.py`, change the resources import to:

```python
from coscience.resources import PLATFORM_KEYS, effective_requirement
```

Replace `select_grants` with:

```python
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
```

Replace `select_yield_victims` with:

```python
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
```

- [ ] **Step 4: Run the scheduler tests, old and new**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_scheduler_hosts.py tests/test_scheduler_grants.py tests/test_scheduler_preempt.py tests/test_scheduler_workers.py -q`
Expected: PASS

- [ ] **Step 5: Commit (only with Oleg's approval)**

```bash
git add src/coscience/scheduler.py tests/test_scheduler_hosts.py
git commit -m "feat(compute): place grants and yields on one host (O2)"
```

---

### Task 4: Dispatcher and sprint page use the sprint's program

**Files:**
- Modify: `src/coscience/dispatcher.py:120-124` (acquire) and `:204` (`over_capacity`)
- Modify: `src/coscience/service.py:355` (`unrunnable`)
- Test: `tests/test_unrunnable.py` (append)

**Interfaces:**
- Consumes: `Ledger.acquire(..., program=)`, `over_capacity(required, pool, program)` (Tasks 1–2).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_unrunnable.py`:

```python
def test_the_cycle_places_a_sprint_only_on_a_host_its_program_may_use(substrate, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "big": {"ssh": "big", "programs": ["p2"], "capacity": {"cpu": 16}}}})
    substrate.save_sprint(Sprint(id="p5-big", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"cpu": 16.0}, program="p5"))
    substrate.save_sprint(Sprint(id="p2-big", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"cpu": 16.0}, program="p2"))
    disp = Dispatcher(substrate, FakeAgent(linger=50, finished=False), pool,
                      SchedulerPolicy(aging_interval=0.0))

    report = disp.run_one_cycle(now=0.0)

    assert report.unrunnable == ["p5-big"]
    assert disp.ledger.lease_for("p2-big").host == "big"


def test_the_sprint_page_judges_capacity_by_the_hosts_its_program_may_use(substrate, every_host_placeable):
    from coscience.service import Service
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(
        "cpu: 4\nhosts:\n  big:\n    ssh: big\n    programs: [p2]\n    capacity: {cpu: 16}\n")
    substrate.save_sprint(Sprint(id="p5-big", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"cpu": 16.0}, program="p5"))
    substrate.save_sprint(Sprint(id="p2-big", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"cpu": 16.0}, program="p2"))
    svc = Service(substrate.repo_root)
    assert svc.get_sprint("p5-big")["unrunnable"] == "needs cpu 16 but capacity is 4"
    assert svc.get_sprint("p2-big")["unrunnable"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_unrunnable.py -q`
Expected: FAIL — the two new tests. Without the program, `big` (reserved for p2) is never a candidate: the cycle reports both `p2-big` and `p5-big` as unrunnable and grants neither, and `get_sprint("p2-big")["unrunnable"]` is `"needs cpu 16 but capacity is 4"`.

- [ ] **Step 3: Implement**

In `src/coscience/dispatcher.py`, change the grant's acquire call to:

```python
            if self.ledger.acquire(sprint.id,
                                   effective_requirement(sprint.resources_required,
                                                         self.ledger.pool),
                                   now, ttl,
                                   priority=eff, preemptible=sprint.preemptible,
                                   program=sprint.program):
```

and the unrunnable check to:

```python
            if over_capacity(s.resources_required, self.ledger.pool, s.program):
```

In `src/coscience/service.py`, change

```python
        return describe_over_capacity(over_capacity(sprint.resources_required, pool))
```

to

```python
        return describe_over_capacity(over_capacity(sprint.resources_required, pool,
                                                    sprint.program))
```

- [ ] **Step 4: Run the dispatcher-facing tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_unrunnable.py tests/test_dispatcher.py tests/test_dispatcher_reconcile.py tests/test_dispatcher_hibernate.py tests/test_integration_phase1.py tests/test_cli_dispatch.py -q`
Expected: PASS

- [ ] **Step 5: Commit (only with Oleg's approval)**

```bash
git add src/coscience/dispatcher.py src/coscience/service.py tests/test_unrunnable.py
git commit -m "feat(compute): grant and judge sprints by their program's hosts (O2)"
```

---

### Task 5: The PM sees only its program's hosts

**Files:**
- Modify: `src/coscience/pm_agent.py:274` (call) and `:296-312` (`_PLATFORM_KEYS`, `_compute`)
- Test: `tests/test_pm_compute.py` (append)

**Interfaces:**
- Consumes: `load_pool`, `PLATFORM_KEYS`, `ResourcePool.placeable_hosts`, `Ledger.used(host)` (Tasks 1–2).
- Produces: `_compute(substrate, program_id: str) -> tuple[dict, dict]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pm_compute.py`:

```python
def test_a_program_is_told_only_the_hosts_it_may_use(substrate, every_host_placeable):
    substrate.save_program(Program(id="p2", title="P2", goals="g"))
    substrate.save_program(Program(id="p5", title="P5", goals="g"))
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(
        "cpu: 24\ngpu: 1\nworkers: 3\n"
        "hosts:\n  remote1:\n    ssh: remote1\n    programs: [p2]\n    capacity: {cpu: 28}\n")
    (cos / "leases.json").write_text(json.dumps([{
        "id": "l1", "sprint_id": "p2-c1", "amounts": {"cpu": 28.0, "workers": 1.0},
        "granted_at": 0.0, "expires_at": 1e12, "priority": 0, "preemptible": True,
        "host": "remote1"}]))

    p2, p5 = gather_context(substrate, "p2"), gather_context(substrate, "p5")

    assert p2.compute_capacity == {"cpu": 52.0, "gpu": 1.0}
    assert p2.compute_leased == {"cpu": 28.0}
    assert p5.compute_capacity == {"cpu": 24.0, "gpu": 1.0}
    assert p5.compute_leased == {}          # remote1's lease is on a host p5 never gets
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_compute.py -q`
Expected: FAIL — `p5.compute_capacity == {"cpu": 52.0, "gpu": 1.0}`

- [ ] **Step 3: Implement**

In `src/coscience/pm_agent.py`, change the call in `gather_context` from

```python
    capacity, leased = _compute(substrate)
```

to

```python
    capacity, leased = _compute(substrate, program_id)
```

Replace `_PLATFORM_KEYS` and `_compute` with:

```python
def _compute(substrate, program_id: str) -> tuple[dict, dict]:
    """(capacity, currently leased) of the resources a sprint in this program can
    request — only the hosts it may be placed on, so a reserved machine is never
    planned around by a program that will not get it."""
    from coscience.ledger import Ledger
    from coscience.resources import PLATFORM_KEYS, load_pool
    pool = load_pool(substrate.repo_root)
    hosts = pool.placeable_hosts(program_id)
    capacity: dict[str, float] = {}
    for h in hosts:
        for k, v in h.capacity.items():
            capacity[k] = capacity.get(k, 0.0) + v
    leased: dict[str, float] = {}
    try:
        ledger = Ledger(pool, substrate.repo_root / ".coscience" / "leases.json")
        ledger.load()
        for h in hosts:
            for k, v in ledger.used(h.name).items():
                if k not in PLATFORM_KEYS:
                    leased[k] = leased.get(k, 0.0) + v
    except (OSError, ValueError, TypeError, KeyError):
        leased = {}
    leased = {k: v for k, v in leased.items() if k in capacity and v}
    return capacity, leased
```

- [ ] **Step 4: Confirm nothing else uses the removed constant**

Run: `grep -rn "_PLATFORM_KEYS" src/ tests/`
Expected: no output

- [ ] **Step 5: Run the PM compute tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_compute.py -q`
Expected: PASS

- [ ] **Step 6: Commit (only with Oleg's approval)**

```bash
git add src/coscience/pm_agent.py tests/test_pm_compute.py
git commit -m "feat(compute): tell the PM only its program's hosts (O2)"
```

---

### Task 6: The API shows hosts, and a capacity edit keeps them

**Files:**
- Modify: `src/coscience/service.py:1605-1668` (`ledger_status`, `set_capacity`)
- Test: `tests/test_service_capacity.py` (append)

**Interfaces:**
- Consumes: `ResourcePool.hosts`, `Host` fields, `Ledger.available(host)`, `Lease.host` (Tasks 1–2).
- Produces: `GET /api/ledger` gains `hosts: [{name, ssh, placeable, programs, run_root, capacity, available}]` and each lease gains `host`. `PUT /api/capacity` leaves a `hosts:` section in the file untouched and refuses a resource named `hosts`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_service_capacity.py`:

```python
HOSTS_YAML = ("cpu: 24\nworkers: 3\n"
              "hosts:\n  remote1:\n    ssh: remote1\n    programs: [p2]\n"
              "    run_root: ~/coscience-runs\n    capacity: {cpu: 28}\n")


def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_ledger_status_lists_every_host(tmp_path):
    _write(tmp_path, HOSTS_YAML)
    status = Service(tmp_path).ledger_status()
    assert status["hosts"] == [
        {"name": "local", "ssh": "", "placeable": True, "programs": [], "run_root": "",
         "capacity": {"cpu": 24.0}, "available": {"cpu": 24.0, "workers": 3.0}},
        {"name": "remote1", "ssh": "remote1", "placeable": False, "programs": ["p2"],
         "run_root": "~/coscience-runs", "capacity": {"cpu": 28.0}, "available": {}},
    ]


def test_ledger_status_names_each_leases_host(tmp_path):
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    led = Ledger(ResourcePool({"cpu": 4.0}), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"cpu": 1.0}, now=0.0, ttl=60.0)
    assert Service(tmp_path).ledger_status()["leases"][0]["host"] == "local"


def test_set_capacity_keeps_the_hosts_section(tmp_path):
    _write(tmp_path, HOSTS_YAML)
    Service(tmp_path).set_capacity({"cpu": 16, "workers": 2})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["cpu"] == 16.0 and written["workers"] == 2.0
    assert written["hosts"]["remote1"]["capacity"] == {"cpu": 28}


def test_set_capacity_refuses_a_resource_named_hosts(tmp_path):
    with pytest.raises(ValueError, match="'hosts' is reserved"):
        Service(tmp_path).set_capacity({"hosts": 1})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_capacity.py -q`
Expected: FAIL — `KeyError: 'hosts'`, lease has no `host` key, `hosts` missing from the written file, and no error for a resource named `hosts`.

- [ ] **Step 3: Implement `ledger_status`**

Replace `ledger_status` in `src/coscience/service.py` with:

```python
    def ledger_status(self) -> dict:
        from coscience.pause import is_paused
        ledger = self._ledger()
        return {
            "capacity": dict(ledger.pool.capacity),
            "used": ledger.used(),
            "available": ledger.available(),
            "paused": is_paused(self.substrate.repo_root),
            "hosts": [
                {"name": h.name, "ssh": h.ssh, "placeable": h.placeable,
                 "programs": list(h.programs), "run_root": h.run_root,
                 "capacity": dict(h.capacity),
                 # A host that cannot take work has nothing available to grant.
                 "available": ledger.available(h.name) if h.placeable else {}}
                for h in ledger.pool.hosts
            ],
            "leases": [
                {"id": l.id, "sprint_id": l.sprint_id, "amounts": l.amounts,
                 "granted_at": l.granted_at, "expires_at": l.expires_at,
                 "priority": l.priority, "preemptible": l.preemptible,
                 "host": l.host}
                for l in ledger.all_leases()
            ],
        }
```

- [ ] **Step 4: Implement `set_capacity` keeping `hosts:`**

In `set_capacity`, directly after the existing `if key == "resources":` block, add:

```python
            if key == "hosts":
                raise ValueError("'hosts' is reserved for remote machines and can't be a resource name")
```

Then replace the block from `path = self.repo_root / ".coscience" / "resources.yaml"` through the `finally:` clause with:

```python
        path = self.repo_root / ".coscience" / "resources.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        # The editor edits this machine's amounts and the platform keys; remote
        # hosts are declared by hand and must survive the edit untouched.
        out: dict = dict(clean)
        if path.is_file():
            loaded = yaml.safe_load(path.read_text()) or {}
            if isinstance(loaded, dict):
                wrapped = loaded.get("resources")
                hosts = (wrapped.get("hosts") if isinstance(wrapped, dict) else None) \
                    or loaded.get("hosts")
                if hosts:
                    out["hosts"] = hosts
        # Unique per call: PUT /api/capacity is a sync route, so FastAPI runs it
        # in a threadpool and concurrent calls are genuinely concurrent. A shared
        # tmp name lets one thread's os.replace pull the file out from under
        # another thread's write/replace.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            tmp.write_text(yaml.safe_dump(out, sort_keys=True))
            os.replace(tmp, path)  # atomic: a dispatcher reading it never sees a partial file
        finally:
            tmp.unlink(missing_ok=True)
```

- [ ] **Step 5: Run the service, HTTP and MCP tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_capacity.py tests/test_http_capacity.py tests/test_service_ledger.py tests/test_http_api.py tests/test_mcp_server.py -q`
Expected: PASS

- [ ] **Step 6: Commit (only with Oleg's approval)**

```bash
git add src/coscience/service.py tests/test_service_capacity.py
git commit -m "feat(compute): show hosts on the ledger and keep them across capacity edits (O2)"
```

---

### Task 7: Whole suite and live read-back

**Files:** none changed.

- [ ] **Step 1: Run the full Python suite**

Run: `~/venvs/coscience/bin/python -m pytest -q`
Expected: PASS (exit 0)

- [ ] **Step 2: Confirm the live substrate still reads as one local host**

Run:

```bash
~/venvs/coscience/bin/python -c "
from coscience.resources import load_pool
p = load_pool('$COSCIENCE_REPO')
print(p.capacity); print([(h.name, h.capacity, h.placeable) for h in p.hosts])"
```

Expected: the same `capacity` as today's file (`cpu`, `gpu`, `workers`, `housekeepers`) and exactly one host, `('local', {...cpu, gpu...}, True)`.

- [ ] **Step 3: Move O2 to To QC in `todo.md`**

Under `# To QC`, after O1, add (and remove O2 from `# To Do (sprint)`; bump `version` and `last_updated`):

```markdown
### O2. Model compute as hosts, not one flat pool

`.coscience/resources.yaml` now reads as a `local` host plus an optional `hosts:`
section, every lease names its host, and no grant spans two machines or lands on a
host reserved for another program; remote hosts are not placeable until O6.

**Check:** `GET /api/ledger` on the live service lists one `local` host and every
lease with `host: local`, and `tests/test_scheduler_hosts.py` covers placement,
reservation and yield per host.
```
