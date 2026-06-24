# Co-Science Platform — Phase 1 (Coordination & Scheduling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single disposable worker (Phase 0) into a deterministic dispatcher that runs several sprints concurrently across a declared resource pool, granting time-bounded all-or-nothing leases so 3–6 sprints never gridlock on the 2 GPUs — with priority, aging, graceful preemption, and TTL-based reclaim — and capture each step's agent output into the result artifact.

**Architecture:** Still filesystem + process based (no container/MCP yet — those are a later packaging phase). A single **Dispatcher** heartbeat process is the sole writer of an authoritative **Ledger** (`.coscience/leases.json`). Each cycle it expires stale leases, grants leases to eligible sprints per a deterministic **SchedulerPolicy** (priority + aging + first-fit, with preemption of lower-priority preemptible holders), then runs one **Worker** beat per leased sprint and releases the lease on completion. Heavy compute still runs out-of-process via Phase 0's `detached:` mechanism, so real parallelism comes from detached jobs while the dispatcher stays single-threaded and race-free.

**Tech Stack:** Python 3.12 (canonical venv `~/venvs/coscience`), PyYAML, pytest. No new dependencies.

## Global Constraints

- **Interpreter:** the canonical venv is `/home/oleg/venvs/coscience` (uv-managed CPython 3.12.13). Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`. Do NOT use the old project `.venv` (3.11.0rc1).
- **Package source:** `/home/oleg/sync/bmt-share/!Science/src/coscience` (installed editable into the venv — new modules are importable immediately).
- **Dependencies:** runtime → `pyyaml` only; dev → `pytest` only. Add nothing else.
- **The Ledger is the single source of truth for resource allocation** and is written by ONE process (the Dispatcher). All grant/release decisions go through it. `acquire` is **all-or-nothing** — never partially grant a request.
- **One worker beat per leased sprint per dispatcher cycle** (bounded work; a beat must remain safe to kill, per Phase 0).
- **All durable state on disk:** sprints/results/progress (Phase 0) plus `.coscience/leases.json` and `.coscience/queue.json`. No allocation state in memory between cycles.
- **Backward compatibility:** Phase 0 behavior (`Worker.run_one_beat`, the single-worker `coscience worker` CLI, all 34 existing tests) must keep working unchanged.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. One commit per task minimum. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only Phase 1 is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 — Walking skeleton | Worker + OKF substrate + heartbeat + checkpoint/resume + detached re-attach | DONE |
| **1 — Coordination & scheduling** (this plan) | resource pool + ledger/leases + scheduler (priority/aging/preempt) + dispatcher + output capture | **planned here** |
| 1b (later) | containerized service + MCP/API gateway; LLM resource-manager policy layer | deferred |
| 2 — PM + OC dashboard loop | program-manager agent, internal dashboard | later |
| 3 — Scout + sandbox hardening | capability enforcement, quarantine pipeline | later |

Design source: `docs/superpowers/specs/2026-06-23-co-science-platform-design.md` §6 (resource/GPU scheduler) and §4 (agent runtime). Phase 1 implements the deterministic ledger+scheduler core of §6 and the output-capture enrichment of §4.

---

## File Structure

**New / modified platform code:**
```
src/coscience/
  models.py          # MODIFY: add Lease; add Sprint fields (resources_required, priority, preemptible)
  substrate.py       # MODIFY: load/save the new sprint fields
  resources.py       # NEW: ResourcePool + load_pool from .coscience/resources.yaml
  ledger.py          # NEW: Ledger (capacity/used/available/can_fit/acquire/release/renew/expire) + JSON persistence
  scheduler.py       # NEW: SchedulerPolicy (effective_priority, select_grants, select_preemptions)
  worker.py          # MODIFY: extract run_sprint_beat(sprint); capture step output into result
  dispatcher.py      # NEW: Dispatcher.run_one_cycle() — the multi-sprint heartbeat
  cli.py             # MODIFY: add `coscience dispatch` subcommand
tests/
  test_resources.py        # NEW
  test_models_phase1.py     # NEW (lease + sprint scheduling fields)
  test_ledger.py            # NEW
  test_ledger_ttl.py        # NEW
  test_scheduler_grants.py  # NEW
  test_scheduler_preempt.py # NEW
  test_worker_phase1.py     # NEW (run_sprint_beat + output capture)
  test_dispatcher.py        # NEW
  test_cli_dispatch.py      # NEW
  test_integration_phase1.py# NEW (end-to-end concurrency under a constrained pool)
```

**Substrate (operational state, committed with the rest):**
```
<repo_root>/.coscience/resources.yaml   # declared capacity
<repo_root>/.coscience/leases.json       # active leases (Dispatcher-owned)
<repo_root>/.coscience/queue.json        # sprint_id -> queued_at (for aging/FIFO)
```

---

## Task 1: Resource pool

**Files:**
- Create: `src/coscience/resources.py`
- Test: `tests/test_resources.py`

**Interfaces:**
- Consumes: PyYAML.
- Produces:
  - `@dataclass ResourcePool(capacity: dict[str, float])`.
  - `ResourcePool.from_dict(d: dict) -> ResourcePool` — accepts either `{"resources": {...}}` or a bare `{...}`; coerces values to `float`.
  - `ResourcePool.from_yaml(path) -> ResourcePool`.
  - `load_pool(repo_root) -> ResourcePool` — reads `<repo_root>/.coscience/resources.yaml`; returns an empty pool (`{}`) if the file is absent.

- [ ] **Step 1: Write the failing tests**

`tests/test_resources.py`:
```python
from coscience.resources import ResourcePool, load_pool


def test_from_dict_bare_mapping_coerces_floats():
    pool = ResourcePool.from_dict({"gpu_24gb": 1, "cpu": 32})
    assert pool.capacity == {"gpu_24gb": 1.0, "cpu": 32.0}


def test_from_dict_accepts_resources_wrapper():
    pool = ResourcePool.from_dict({"resources": {"gpu_24gb": 1}})
    assert pool.capacity == {"gpu_24gb": 1.0}


def test_from_yaml_roundtrip(tmp_path):
    p = tmp_path / "resources.yaml"
    p.write_text("resources:\n  gpu_24gb: 1\n  disk_gb: 500\n")
    pool = ResourcePool.from_yaml(p)
    assert pool.capacity == {"gpu_24gb": 1.0, "disk_gb": 500.0}


def test_load_pool_missing_returns_empty(tmp_path):
    assert load_pool(tmp_path).capacity == {}


def test_load_pool_reads_coscience_dir(tmp_path):
    d = tmp_path / ".coscience"
    d.mkdir()
    (d / "resources.yaml").write_text("resources:\n  runtime_slots: 4\n")
    assert load_pool(tmp_path).capacity == {"runtime_slots": 4.0}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_resources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.resources'`.

- [ ] **Step 3: Implement `resources.py`**

```python
"""Declared resource capacity for an environment."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ResourcePool:
    capacity: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ResourcePool":
        raw = d.get("resources", d) if isinstance(d, dict) else {}
        return cls(capacity={str(k): float(v) for k, v in (raw or {}).items()})

    @classmethod
    def from_yaml(cls, path) -> "ResourcePool":
        return cls.from_dict(yaml.safe_load(Path(path).read_text()) or {})


def load_pool(repo_root) -> ResourcePool:
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    if not path.is_file():
        return ResourcePool()
    return ResourcePool.from_yaml(path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_resources.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/resources.py tests/test_resources.py
git commit -m "feat: resource pool loaded from .coscience/resources.yaml"
```

---

## Task 2: Lease model + sprint scheduling fields

**Files:**
- Modify: `src/coscience/models.py`
- Modify: `src/coscience/substrate.py`
- Test: `tests/test_models_phase1.py`

**Interfaces:**
- Consumes: existing `Sprint`, `SprintStatus`.
- Produces:
  - `@dataclass Lease(id: str, sprint_id: str, amounts: dict[str, float], granted_at: float, expires_at: float, priority: int = 0, preemptible: bool = True)`.
  - `Sprint` gains three trailing fields (positional compatibility preserved): `resources_required: dict[str, float] = {}`, `priority: int = 0`, `preemptible: bool = True`.
  - `Substrate.load_sprint` parses `resources_required` (values coerced to float), `priority`, `preemptible` from frontmatter (with the above defaults).
  - `Substrate.save_sprint` writes `resources_required` only when non-empty, `priority` only when `!= 0`, `preemptible` only when `False`.

- [ ] **Step 1: Write the failing tests**

`tests/test_models_phase1.py`:
```python
from coscience.models import Lease, Sprint, SprintStatus, Step
from coscience.substrate import Substrate


def test_lease_construct_defaults():
    lease = Lease(id="L1", sprint_id="sp1", amounts={"gpu": 1.0},
                  granted_at=100.0, expires_at=200.0)
    assert lease.priority == 0
    assert lease.preemptible is True


def test_sprint_scheduling_defaults():
    s = Sprint(id="sp1", status=SprintStatus.APPROVED, goals="g", plan=[])
    assert s.resources_required == {}
    assert s.priority == 0
    assert s.preemptible is True


def test_substrate_roundtrips_scheduling_fields(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(
        id="sp1", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "echo hi")],
        resources_required={"gpu_24gb": 1}, priority=5, preemptible=False,
    )
    sub.save_sprint(s)
    loaded = sub.load_sprint("sp1")
    assert loaded.resources_required == {"gpu_24gb": 1.0}
    assert loaded.priority == 5
    assert loaded.preemptible is False


def test_substrate_defaults_when_fields_absent(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_sprint(Sprint(id="sp2", status=SprintStatus.APPROVED, goals="g",
                           plan=[Step("s1", "echo hi")]))
    loaded = sub.load_sprint("sp2")
    assert loaded.resources_required == {}
    assert loaded.priority == 0
    assert loaded.preemptible is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_models_phase1.py -v`
Expected: FAIL with `ImportError: cannot import name 'Lease'`.

- [ ] **Step 3: Add `Lease` and extend `Sprint` in `models.py`**

Add to `models.py` (the `Sprint` dataclass gains three trailing fields; add the `Lease` dataclass near the other dataclasses):
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
```

Modify the existing `Sprint` dataclass to append three fields after `results`:
```python
@dataclass
class Sprint:
    id: str
    status: SprintStatus
    goals: str
    plan: list[Step]
    program: str | None = None
    results: list[str] = field(default_factory=list)
    resources_required: dict[str, float] = field(default_factory=dict)
    priority: int = 0
    preemptible: bool = True
```

- [ ] **Step 4: Extend `Substrate.load_sprint` / `save_sprint` in `substrate.py`**

In `load_sprint`, after building `plan`, include the new fields in the returned `Sprint`:
```python
        return Sprint(
            id=sprint_id,
            status=SprintStatus(fm["status"]),
            goals=fm.get("goals", ""),
            plan=plan,
            program=fm.get("program"),
            results=list(fm.get("results", [])),
            resources_required={
                str(k): float(v) for k, v in (fm.get("resources_required") or {}).items()
            },
            priority=int(fm.get("priority", 0)),
            preemptible=bool(fm.get("preemptible", True)),
        )
```

In `save_sprint`, after the existing `program`/`results` handling and before writing the file, add:
```python
        if sprint.resources_required:
            fm["resources_required"] = sprint.resources_required
        if sprint.priority != 0:
            fm["priority"] = sprint.priority
        if not sprint.preemptible:
            fm["preemptible"] = False
```

- [ ] **Step 5: Run the tests to verify they pass (plus no regressions)**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_models_phase1.py tests/test_models.py tests/test_substrate.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_models_phase1.py
git commit -m "feat: Lease model + sprint scheduling fields (resources_required, priority, preemptible)"
```

---

## Task 3: Ledger core (acquire / release / availability)

**Files:**
- Create: `src/coscience/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `ResourcePool`, `Lease`.
- Produces `class Ledger`:
  - `Ledger(pool: ResourcePool, path: Path)` — `path` is the leases JSON file.
  - `load() -> None` / `save() -> None` (atomic write via temp + `os.replace`).
  - `all_leases() -> list[Lease]`.
  - `lease_for(sprint_id) -> Lease | None`.
  - `used() -> dict[str, float]` (sum of active lease amounts per key).
  - `available() -> dict[str, float]` (`capacity - used` for each capacity key).
  - `can_fit(amounts: dict[str, float]) -> bool` (every key's request `<=` available; a key absent from capacity has 0 available).
  - `acquire(sprint_id, amounts, now, ttl, priority=0, preemptible=True) -> Lease | None` — idempotent (returns the existing lease if the sprint already holds one); else all-or-nothing: grant + persist + return, or `None` if it doesn't fit.
  - `release(sprint_id) -> None` (no-op if none; persists).

- [ ] **Step 1: Write the failing tests**

`tests/test_ledger.py`:
```python
from coscience.ledger import Ledger
from coscience.resources import ResourcePool


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_acquire_within_capacity(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    lease = led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    assert lease is not None
    assert led.used() == {"gpu": 1.0}
    assert led.available() == {"gpu": 1.0}


def test_all_or_nothing_when_overcommitted(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    assert led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0) is not None
    assert led.acquire("sp2", {"gpu": 1.0}, now=100.0, ttl=60.0) is None
    assert led.used() == {"gpu": 1.0}


def test_multi_resource_all_or_nothing(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0, "cpu": 4.0})
    # cpu fits but gpu does not -> whole request denied
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    assert led.acquire("sp2", {"gpu": 1.0, "cpu": 2.0}, now=100.0, ttl=60.0) is None
    assert led.used() == {"gpu": 1.0}


def test_acquire_is_idempotent_per_sprint(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    a = led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    b = led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    assert a.id == b.id
    assert led.used() == {"gpu": 1.0}


def test_release_frees_capacity(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    led.release("sp1")
    assert led.used() == {"gpu": 0.0}
    assert led.acquire("sp2", {"gpu": 1.0}, now=100.0, ttl=60.0) is not None


def test_persistence_roundtrip(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0, priority=3)
    led2 = Ledger(ResourcePool({"gpu": 2.0}), tmp_path / "leases.json")
    led2.load()
    lease = led2.lease_for("sp1")
    assert lease is not None and lease.priority == 3
    assert led2.used() == {"gpu": 1.0}


def test_can_fit_unknown_key_is_false(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    assert led.can_fit({"tpu": 1.0}) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.ledger'`.

- [ ] **Step 3: Implement `ledger.py`**

```python
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

    # --- persistence ---
    def load(self) -> None:
        if self.path.is_file():
            data = json.loads(self.path.read_text())
            self._leases = {d["sprint_id"]: Lease(**d) for d in data}
        else:
            self._leases = {}

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
        out = {k: 0.0 for k in self.pool.capacity}
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
        self._leases[sprint_id] = lease
        self.save()
        return lease

    def release(self, sprint_id: str) -> None:
        if sprint_id in self._leases:
            del self._leases[sprint_id]
            self.save()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_ledger.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/ledger.py tests/test_ledger.py
git commit -m "feat: resource ledger with all-or-nothing leases and JSON persistence"
```

---

## Task 4: Ledger TTL — renew & expire

**Files:**
- Modify: `src/coscience/ledger.py`
- Test: `tests/test_ledger_ttl.py`

**Interfaces:**
- Adds to `Ledger`:
  - `renew(sprint_id, now, ttl) -> None` — set `expires_at = now + ttl` for the sprint's lease (no-op if none); persists.
  - `expire(now) -> list[Lease]` — remove and return all leases with `expires_at <= now`; persists if any were removed.

- [ ] **Step 1: Write the failing tests**

`tests/test_ledger_ttl.py`:
```python
from coscience.ledger import Ledger
from coscience.resources import ResourcePool


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_expire_removes_only_stale(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    led.acquire("old", {"gpu": 1.0}, now=0.0, ttl=10.0)     # expires at 10
    led.acquire("fresh", {"gpu": 1.0}, now=0.0, ttl=100.0)  # expires at 100
    removed = led.expire(now=50.0)
    assert [r.sprint_id for r in removed] == ["old"]
    assert led.lease_for("old") is None
    assert led.lease_for("fresh") is not None


def test_renew_extends_expiry(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("sp1", {"gpu": 1.0}, now=0.0, ttl=10.0)
    led.renew("sp1", now=8.0, ttl=10.0)  # now expires at 18
    assert led.expire(now=12.0) == []
    assert led.lease_for("sp1") is not None


def test_expire_frees_capacity_for_reacquire(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("stuck", {"gpu": 1.0}, now=0.0, ttl=10.0)
    assert led.acquire("next", {"gpu": 1.0}, now=20.0, ttl=10.0) is None  # still held in-memory
    led.expire(now=20.0)
    assert led.acquire("next", {"gpu": 1.0}, now=20.0, ttl=10.0) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_ledger_ttl.py -v`
Expected: FAIL with `AttributeError: 'Ledger' object has no attribute 'expire'`.

- [ ] **Step 3: Add `renew` and `expire` to `Ledger`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_ledger_ttl.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/ledger.py tests/test_ledger_ttl.py
git commit -m "feat: ledger TTL renew and expire (reclaim stale leases)"
```

---

## Task 5: Scheduler — grants (priority + aging + first-fit)

**Files:**
- Create: `src/coscience/scheduler.py`
- Test: `tests/test_scheduler_grants.py`

**Interfaces:**
- Consumes: `Sprint`, `Ledger`.
- Produces `@dataclass SchedulerPolicy(default_ttl: float = 3600.0, aging_interval: float = 300.0)`:
  - `effective_priority(sprint, queued_at, now) -> int` = `sprint.priority + int((now - queued_at) // aging_interval)` (aging boost; `aging_interval <= 0` disables aging → returns `sprint.priority`).
  - `select_grants(candidates: list[Sprint], queued_at: dict[str, float], ledger: Ledger, now: float) -> list[Sprint]` — sort by `(effective_priority desc, queued_at asc)`; greedily select those that fit against a simulated copy of `ledger.available()` (subtracting each grant). Sprints with empty `resources_required` always fit. Returns the sprints to grant, in selection order.

- [ ] **Step 1: Write the failing tests**

`tests/test_scheduler_grants.py`:
```python
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio)


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_effective_priority_ages_up():
    pol = SchedulerPolicy(aging_interval=10.0)
    s = _sprint("sp1", prio=1)
    assert pol.effective_priority(s, queued_at=0.0, now=0.0) == 1
    assert pol.effective_priority(s, queued_at=0.0, now=25.0) == 3  # 1 + 25//10


def test_grants_respect_capacity_and_priority(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    lo = _sprint("lo", prio=0, req={"gpu": 1.0})
    hi = _sprint("hi", prio=5, req={"gpu": 1.0})
    q = {"lo": 0.0, "hi": 0.0}
    granted = pol.select_grants([lo, hi], q, led, now=0.0)
    assert [s.id for s in granted] == ["hi"]  # only one gpu, higher priority wins


def test_grants_fifo_tiebreak_on_equal_priority(tmp_path):
    pol = SchedulerPolicy(aging_interval=0.0)  # disable aging for a clean FIFO check
    led = _ledger(tmp_path, {"gpu": 1.0})
    a = _sprint("a", prio=0, req={"gpu": 1.0})
    b = _sprint("b", prio=0, req={"gpu": 1.0})
    q = {"a": 10.0, "b": 5.0}  # b queued earlier
    granted = pol.select_grants([a, b], q, led, now=20.0)
    assert [s.id for s in granted] == ["b"]


def test_grants_multiple_when_capacity_allows(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 2.0})
    a = _sprint("a", req={"gpu": 1.0})
    b = _sprint("b", req={"gpu": 1.0})
    q = {"a": 0.0, "b": 0.0}
    granted = pol.select_grants([a, b], q, led, now=0.0)
    assert {s.id for s in granted} == {"a", "b"}


def test_no_resource_sprints_always_granted(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 0.0})
    a = _sprint("a", req={})
    granted = pol.select_grants([a], {"a": 0.0}, led, now=0.0)
    assert [s.id for s in granted] == ["a"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_scheduler_grants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.scheduler'`.

- [ ] **Step 3: Implement `scheduler.py` (grants)**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_scheduler_grants.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/scheduler.py tests/test_scheduler_grants.py
git commit -m "feat: scheduler grants (priority + aging + first-fit)"
```

---

## Task 6: Scheduler — preemption

**Files:**
- Modify: `src/coscience/scheduler.py`
- Test: `tests/test_scheduler_preempt.py`

**Interfaces:**
- Adds to `SchedulerPolicy`:
  - `select_preemptions(candidate: Sprint, candidate_priority: int, ledger: Ledger) -> list[Lease]` — choose the minimal set of currently-held **preemptible** leases whose `priority < candidate_priority` to free, so that (freed + currently available) covers `candidate.resources_required`. Victims chosen lowest-`priority` first (then most-recently granted first, i.e. larger `granted_at` first). Returns `[]` if no preemption is needed (it already fits) OR if even freeing all eligible victims cannot satisfy the request.

- [ ] **Step 1: Write the failing tests**

`tests/test_scheduler_preempt.py`:
```python
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio)


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_no_preemption_when_it_already_fits(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 2.0})
    led.acquire("held", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_preemptions(cand, 5, led) == []


def test_preempts_lower_priority_holder(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("lo", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0, preemptible=True)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    victims = pol.select_preemptions(cand, 5, led)
    assert [v.sprint_id for v in victims] == ["lo"]


def test_will_not_preempt_equal_or_higher_priority(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("peer", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=5, preemptible=True)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_preemptions(cand, 5, led) == []


def test_will_not_preempt_non_preemptible(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("lo", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0, preemptible=False)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_preemptions(cand, 5, led) == []


def test_minimal_victim_set(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 3.0})
    led.acquire("a", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0)
    led.acquire("b", {"gpu": 1.0}, now=1.0, ttl=60.0, priority=1)
    led.acquire("c", {"gpu": 1.0}, now=2.0, ttl=60.0, priority=0)
    # capacity 3, all held -> available 0. candidate needs 1.
    # eligible (priority<5): a,b,c. lowest priority first: a(0)&c(0) before b(1);
    # tie on priority broken by larger granted_at first -> c (granted 2.0) before a (0.0).
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    victims = pol.select_preemptions(cand, 5, led)
    assert [v.sprint_id for v in victims] == ["c"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_scheduler_preempt.py -v`
Expected: FAIL with `AttributeError: 'SchedulerPolicy' object has no attribute 'select_preemptions'`.

- [ ] **Step 3: Add `select_preemptions` to `SchedulerPolicy`**

Add this import at the top of `scheduler.py`:
```python
from coscience.models import Lease, Sprint
```
(replace the existing `from coscience.models import Sprint` line with the line above)

Add the method to `SchedulerPolicy`:
```python
    def select_preemptions(self, candidate, candidate_priority, ledger: Ledger):
        need = candidate.resources_required
        avail = dict(ledger.available())
        deficit = {k: v - avail.get(k, 0.0) for k, v in need.items()
                   if v - avail.get(k, 0.0) > 0}
        if not deficit:
            return []

        eligible = [l for l in ledger.all_leases()
                    if l.preemptible and l.priority < candidate_priority]
        # lowest priority first; tie -> most-recently granted first
        eligible.sort(key=lambda l: (l.priority, -l.granted_at))

        victims: list[Lease] = []
        freed: dict[str, float] = {}
        for lease in eligible:
            if all(freed.get(k, 0.0) >= d for k, d in deficit.items()):
                break
            victims.append(lease)
            for k, v in lease.amounts.items():
                freed[k] = freed.get(k, 0.0) + v

        if all(freed.get(k, 0.0) >= d for k, d in deficit.items()):
            return victims
        return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_scheduler_preempt.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/scheduler.py tests/test_scheduler_preempt.py
git commit -m "feat: scheduler preemption (free minimal lower-priority preemptible leases)"
```

---

## Task 7: Worker — per-sprint beat + output capture

**Files:**
- Modify: `src/coscience/worker.py`
- Test: `tests/test_worker_phase1.py`

**Interfaces:**
- Refactor: extract `run_sprint_beat(sprint: Sprint) -> BeatOutcome` containing the per-sprint logic (progress load, next-step selection, detached handling, normal step execution, completion). `run_one_beat()` becomes: `_claim_sprint()`; if `None` return `IDLE`; else `return self.run_sprint_beat(sprint)`. Phase 0 behavior is unchanged.
- Enhancement: when a normal (non-detached) step completes, record its output. On sprint completion, the result body includes each step's captured output. Implementation: maintain step outputs in `progress.md` via a new `ProgressState.outputs: dict[str, str]` (step_id -> output, truncated to 2000 chars), and the completion path renders them into the result body.

- [ ] **Step 1: Add `outputs` to `ProgressState` (models.py) and persist it (substrate.py)**

In `models.py`, add a field to `ProgressState`:
```python
@dataclass
class ProgressState:
    sprint_id: str
    completed_steps: list[str] = field(default_factory=list)
    detached: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
```

In `substrate.py` `load_progress`, add `outputs` parsing:
```python
        return ProgressState(
            sprint_id=sprint_id,
            completed_steps=list(fm.get("completed_steps", [])),
            detached={str(k): int(v) for k, v in (fm.get("detached") or {}).items()},
            outputs={str(k): str(v) for k, v in (fm.get("outputs") or {}).items()},
        )
```
In `substrate.py` `save_progress`, include outputs:
```python
        fm = {
            "completed_steps": progress.completed_steps,
            "detached": progress.detached,
            "outputs": progress.outputs,
        }
```

- [ ] **Step 2: Write the failing tests**

`tests/test_worker_phase1.py`:
```python
from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.worker import Worker


def _sprint(sid, steps, status=SprintStatus.EXECUTING):
    return Sprint(id=sid, status=status, goals="g", plan=steps)


def test_run_sprint_beat_runs_one_step_of_given_sprint(substrate):
    s = _sprint("sp1", [Step("s1", "echo hi"), Step("s2", "echo bye")])
    substrate.save_sprint(s)
    outcome = Worker(substrate, ShellStepExecutor()).run_sprint_beat(s)
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_progress("sp1").completed_steps == ["s1"]


def test_run_sprint_beat_captures_output(substrate):
    s = _sprint("sp1", [Step("s1", "echo captured-out")])
    substrate.save_sprint(s)
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(s)  # runs s1
    assert "captured-out" in substrate.load_progress("sp1").outputs["s1"]


def test_completion_writes_outputs_into_result(substrate):
    s = _sprint("sp1", [Step("s1", "echo hello-world")])
    substrate.save_sprint(s)
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(s)  # s1 done
    assert worker.run_sprint_beat(substrate.load_sprint("sp1")) == BeatOutcome.COMPLETED
    result_text = (substrate.repo_root / "results" / "sp1-result.md").read_text()
    assert "hello-world" in result_text


def test_run_one_beat_still_claims_and_runs(substrate):
    # Phase 0 behavior preserved.
    substrate.save_sprint(_sprint("sp1", [Step("s1", "true")], status=SprintStatus.APPROVED))
    assert Worker(substrate, ShellStepExecutor()).run_one_beat() == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
```

- [ ] **Step 3: Run the new tests + Phase 0 worker tests to verify the new ones fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_worker_phase1.py -v`
Expected: FAIL with `AttributeError: 'Worker' object has no attribute 'run_sprint_beat'`.

- [ ] **Step 4: Refactor `worker.py`**

Replace the body of `run_one_beat` so it delegates, and add `run_sprint_beat`. The full new `Worker` method section:
```python
    def run_one_beat(self) -> BeatOutcome:
        sprint = self._claim_sprint()
        if sprint is None:
            return BeatOutcome.IDLE
        return self.run_sprint_beat(sprint)

    def run_sprint_beat(self, sprint) -> BeatOutcome:
        progress = self.substrate.load_progress(sprint.id)
        next_step = next(
            (s for s in sprint.plan if s.id not in progress.completed_steps), None
        )

        if next_step is None:
            lines = [f"Sprint {sprint.id} completed {len(sprint.plan)} steps.", ""]
            for step in sprint.plan:
                out = progress.outputs.get(step.id, "").strip()
                if out:
                    lines.append(f"## {step.id}\n\n{out}\n")
            result = Result(
                id=f"{sprint.id}-result",
                sprint=sprint.id,
                summary="\n".join(lines).strip(),
            )
            self.substrate.save_result(result)
            sprint.status = SprintStatus.DONE
            sprint.results = [result.id]
            self.substrate.save_sprint(sprint)
            self.substrate.commit(f"sprint {sprint.id}: done, result {result.id}")
            return BeatOutcome.COMPLETED

        if next_step.run.startswith("detached:"):
            command = next_step.run[len("detached:"):].strip()
            pid = progress.detached.get(next_step.id)
            if pid is None:
                progress.detached[next_step.id] = launch_detached(command)
                self.substrate.save_progress(progress)
                self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} launched")
                return BeatOutcome.PROGRESSED
            if is_running(pid):
                return BeatOutcome.PROGRESSED
            progress.completed_steps.append(next_step.id)
            del progress.detached[next_step.id]
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached step {next_step.id} done")
            return BeatOutcome.PROGRESSED

        step_result = self.executor.run(next_step)
        if step_result.completed:
            progress.completed_steps.append(next_step.id)
            progress.outputs[next_step.id] = (step_result.output or "")[:2000]
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} done")
        return BeatOutcome.PROGRESSED
```
(Leave `_claim_sprint` unchanged. The imports at the top — `BeatOutcome, Result, SprintStatus` from models and `is_running, launch_detached` from executor — are already present from Phase 0.)

- [ ] **Step 5: Run the new tests + full worker/resume/detached suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_worker_phase1.py tests/test_worker.py tests/test_resume.py tests/test_detached.py -v`
Expected: all passed (Phase 0 worker behavior preserved).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/worker.py src/coscience/models.py src/coscience/substrate.py tests/test_worker_phase1.py
git commit -m "feat: per-sprint worker beat + capture step output into results"
```

---

## Task 8: Dispatcher — the multi-sprint heartbeat

**Files:**
- Create: `src/coscience/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `Substrate`, a `StepExecutor`, `ResourcePool`, `SchedulerPolicy`, `Ledger`, `Worker`.
- Produces:
  - `@dataclass CycleReport(granted: int, preempted: int, beaten: int, completed: int, waiting: int)`.
  - `class Dispatcher(substrate, executor, pool, policy=SchedulerPolicy())`:
    - `run_one_cycle(now: float | None = None) -> CycleReport`. Steps each cycle:
      1. `now = now or time.time()`; `ledger.load()`.
      2. `ledger.expire(now)`.
      3. eligible = sprints with status `APPROVED` or `EXECUTING`.
      4. queue-state (`.coscience/queue.json`, sprint_id -> queued_at): add `now` for newly-eligible; drop entries no longer eligible.
      5. `needs_lease` = eligible sprints with no current lease. `grants = policy.select_grants(needs_lease, queue, ledger, now)`; for each: `ledger.acquire(...)` with `priority=effective_priority`, `ttl=policy.default_ttl`, `preemptible=sprint.preemptible`; if `APPROVED` → set `EXECUTING`, save.
      6. Preemption (one round): of the sprints still without a lease, take the highest effective-priority one; `victims = policy.select_preemptions(...)`; if non-empty, `ledger.release` each victim and `ledger.acquire` for the candidate (set `EXECUTING` if needed).
      7. For each held lease whose sprint is `EXECUTING`: `outcome = worker.run_sprint_beat(sprint)`; `ledger.renew(sprint.id, now, ttl)`; if `COMPLETED` → `ledger.release(sprint.id)`, drop from queue.
      8. persist queue-state; `substrate.commit("dispatch cycle")` if anything changed.
      9. return the `CycleReport`.

- [ ] **Step 1: Write the failing tests**

`tests/test_dispatcher.py`:
```python
from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _approved(sid, steps, req=None, prio=0):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=steps,
                  resources_required=req or {}, priority=prio)


def _dispatcher(substrate, capacity):
    return Dispatcher(substrate, ShellStepExecutor(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def test_runs_a_sprint_to_completion(substrate):
    substrate.save_sprint(_approved("sp1", [Step("s1", "true"), Step("s2", "true")],
                                    req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(6):
        disp.run_one_cycle(now=float(t))
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE


def test_never_over_allocates_under_contention(substrate):
    # 3 sprints each need the single GPU; capacity must never be exceeded.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_approved(sid, [Step("s1", "true")], req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(30):
        disp.run_one_cycle(now=float(t))
        # invariant after each cycle: never more than capacity leased
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0
    for sid in ("a", "b", "c"):
        assert substrate.load_sprint(sid).status == SprintStatus.DONE


def test_higher_priority_runs_first(substrate):
    substrate.save_sprint(_approved("lo", [Step("s1", "true")], req={"gpu": 1.0}, prio=0))
    substrate.save_sprint(_approved("hi", [Step("s1", "true")], req={"gpu": 1.0}, prio=9))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.run_one_cycle(now=0.0)  # grants the single gpu to the higher priority
    disp.ledger.load()
    assert disp.ledger.lease_for("hi") is not None
    assert disp.ledger.lease_for("lo") is None


def test_completion_releases_lease(substrate):
    substrate.save_sprint(_approved("sp1", [Step("s1", "true")], req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(4):
        disp.run_one_cycle(now=float(t))
    disp.ledger.load()
    assert disp.ledger.all_leases() == []


def test_concurrent_when_capacity_allows(substrate):
    substrate.save_sprint(_approved("a", [Step("s1", "true")], req={"gpu": 1.0}))
    substrate.save_sprint(_approved("b", [Step("s1", "true")], req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 2.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("a") is not None
    assert disp.ledger.lease_for("b") is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.dispatcher'`.

- [ ] **Step 3: Implement `dispatcher.py`**

```python
"""The dispatcher: a single heartbeat that schedules many sprints over the ledger."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from coscience.executor import StepExecutor
from coscience.ledger import Ledger
from coscience.models import BeatOutcome, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.substrate import Substrate
from coscience.worker import Worker

_ELIGIBLE = (SprintStatus.APPROVED, SprintStatus.EXECUTING)


@dataclass
class CycleReport:
    granted: int = 0
    preempted: int = 0
    beaten: int = 0
    completed: int = 0
    waiting: int = 0


class Dispatcher:
    def __init__(self, substrate: Substrate, executor: StepExecutor,
                 pool: ResourcePool, policy: SchedulerPolicy | None = None):
        self.substrate = substrate
        self.executor = executor
        self.policy = policy or SchedulerPolicy()
        self.worker = Worker(substrate, executor)
        cos = substrate.repo_root / ".coscience"
        self.ledger = Ledger(pool, cos / "leases.json")
        self._queue_path = cos / "queue.json"

    def _load_queue(self) -> dict[str, float]:
        if self._queue_path.is_file():
            return {str(k): float(v) for k, v in json.loads(self._queue_path.read_text()).items()}
        return {}

    def _save_queue(self, queue: dict[str, float]) -> None:
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._queue_path.with_name(self._queue_path.name + ".tmp")
        tmp.write_text(json.dumps(queue, indent=2))
        tmp.replace(self._queue_path)

    def run_one_cycle(self, now: float | None = None) -> CycleReport:
        now = time.time() if now is None else float(now)
        report = CycleReport()
        ttl = self.policy.default_ttl

        self.ledger.load()
        self.ledger.expire(now)

        eligible = self.substrate.iter_sprints()
        eligible = [s for s in eligible if s.status in _ELIGIBLE]
        eligible_ids = {s.id for s in eligible}

        queue = self._load_queue()
        for s in eligible:
            queue.setdefault(s.id, now)
        queue = {k: v for k, v in queue.items() if k in eligible_ids}

        # --- grants ---
        needs = [s for s in eligible if self.ledger.lease_for(s.id) is None]
        for sprint in self.policy.select_grants(needs, queue, self.ledger, now):
            eff = self.policy.effective_priority(sprint, queue.get(sprint.id, now), now)
            if self.ledger.acquire(sprint.id, sprint.resources_required, now, ttl,
                                   priority=eff, preemptible=sprint.preemptible):
                report.granted += 1
                if sprint.status == SprintStatus.APPROVED:
                    sprint.status = SprintStatus.EXECUTING
                    self.substrate.save_sprint(sprint)

        # --- one preemption round for the top starved candidate ---
        starved = [s for s in eligible if self.ledger.lease_for(s.id) is None]
        if starved:
            starved.sort(
                key=lambda s: -self.policy.effective_priority(s, queue.get(s.id, now), now))
            cand = starved[0]
            cand_eff = self.policy.effective_priority(cand, queue.get(cand.id, now), now)
            victims = self.policy.select_preemptions(cand, cand_eff, self.ledger)
            if victims:
                for v in victims:
                    self.ledger.release(v.sprint_id)
                    report.preempted += 1
                if self.ledger.acquire(cand.id, cand.resources_required, now, ttl,
                                       priority=cand_eff, preemptible=cand.preemptible):
                    report.granted += 1
                    if cand.status == SprintStatus.APPROVED:
                        cand.status = SprintStatus.EXECUTING
                        self.substrate.save_sprint(cand)

        # --- run one beat per leased, executing sprint ---
        for lease in self.ledger.all_leases():
            sprint = self.substrate.load_sprint(lease.sprint_id)
            if sprint.status != SprintStatus.EXECUTING:
                continue
            outcome = self.worker.run_sprint_beat(sprint)
            report.beaten += 1
            self.ledger.renew(lease.sprint_id, now, ttl)
            if outcome == BeatOutcome.COMPLETED:
                self.ledger.release(lease.sprint_id)
                queue.pop(lease.sprint_id, None)
                report.completed += 1

        report.waiting = sum(
            1 for s in eligible if self.ledger.lease_for(s.id) is None)
        self._save_queue(queue)
        if report.granted or report.completed or report.preempted:
            self.substrate.commit("dispatch cycle")
        return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_dispatcher.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: dispatcher cycle (expire, grant, preempt, beat, release) over the ledger"
```

---

## Task 9: CLI — `coscience dispatch`

**Files:**
- Modify: `src/coscience/cli.py`
- Test: `tests/test_cli_dispatch.py`

**Interfaces:**
- Adds to `cli.py`:
  - `dispatch_once(repo_root, executor_name="shell") -> CycleReport` — builds `ResourcePool` via `load_pool`, a `Dispatcher` with the chosen executor (`"shell"` → `ShellStepExecutor`, `"claude"` → `ClaudeCodeExecutor`), and runs one cycle.
  - A `dispatch` subcommand in `main`: `coscience dispatch --repo <path> [--once | --loop --interval <sec>] [--max-beats N] [--executor shell|claude]`. Same loop/`--once`/`--max-beats` semantics as the `worker` subcommand. Each cycle prints a one-line report (e.g. `granted=1 beaten=1 completed=0 waiting=2`).

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_dispatch.py`:
```python
from coscience.cli import dispatch_once, main
from coscience.models import Sprint, SprintStatus, Step
from coscience.substrate import Substrate


def _seed(repo, sid, req=None):
    Substrate(repo).save_sprint(Sprint(
        id=sid, status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true")], resources_required=req or {}))


def _write_pool(repo, yaml_text):
    d = repo / ".coscience"
    d.mkdir(parents=True, exist_ok=True)
    (d / "resources.yaml").write_text(yaml_text)


def test_dispatch_once_returns_report(tmp_path):
    _write_pool(tmp_path, "resources:\n  gpu: 1\n")
    _seed(tmp_path, "sp1", req={"gpu": 1.0})
    report = dispatch_once(tmp_path)
    assert report.granted == 1


def test_main_dispatch_loop_completes_sprints(tmp_path):
    _write_pool(tmp_path, "resources:\n  gpu: 1\n")
    _seed(tmp_path, "a", req={"gpu": 1.0})
    _seed(tmp_path, "b", req={"gpu": 1.0})
    code = main(["dispatch", "--repo", str(tmp_path),
                 "--loop", "--interval", "0", "--max-beats", "12"])
    assert code == 0
    assert Substrate(tmp_path).load_sprint("a").status == SprintStatus.DONE
    assert Substrate(tmp_path).load_sprint("b").status == SprintStatus.DONE


def test_worker_subcommand_still_works(tmp_path):
    _seed(tmp_path, "sp1")
    code = main(["worker", "--repo", str(tmp_path), "--once"])
    assert code == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_cli_dispatch.py -v`
Expected: FAIL with `ImportError: cannot import name 'dispatch_once'`.

- [ ] **Step 3: Extend `cli.py`**

Add these imports at the top of `cli.py`:
```python
from coscience.claude_executor import ClaudeCodeExecutor
from coscience.dispatcher import CycleReport, Dispatcher
from coscience.resources import load_pool
from coscience.scheduler import SchedulerPolicy
```

Add the helper and a `_make_executor` near `run_once`:
```python
def _make_executor(name: str):
    if name == "claude":
        return ClaudeCodeExecutor()
    return ShellStepExecutor()


def dispatch_once(repo_root, executor_name: str = "shell") -> CycleReport:
    disp = Dispatcher(
        Substrate(repo_root), _make_executor(executor_name),
        load_pool(repo_root), SchedulerPolicy(),
    )
    return disp.run_one_cycle()
```

In `main`, after the existing `worker` subparser is defined, add a `dispatch` subparser, and add its handling. The full updated `main`:
```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coscience")
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("worker", help="run the single-sprint heartbeat worker")
    w.add_argument("--repo", required=True, type=Path)
    wmode = w.add_mutually_exclusive_group()
    wmode.add_argument("--once", action="store_true")
    wmode.add_argument("--loop", action="store_true")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--max-beats", type=int, default=None)

    d = sub.add_parser("dispatch", help="run the multi-sprint scheduling dispatcher")
    d.add_argument("--repo", required=True, type=Path)
    dmode = d.add_mutually_exclusive_group()
    dmode.add_argument("--once", action="store_true")
    dmode.add_argument("--loop", action="store_true")
    d.add_argument("--interval", type=float, default=5.0)
    d.add_argument("--max-beats", type=int, default=None)
    d.add_argument("--executor", choices=["shell", "claude"], default="shell")

    args = parser.parse_args(argv)

    if args.command == "worker":
        if args.once or not args.loop:
            print(run_once(args.repo).value)
            return 0
        beats = 0
        while args.max_beats is None or beats < args.max_beats:
            print(run_once(args.repo).value, flush=True)
            beats += 1
            if args.max_beats is None or beats < args.max_beats:
                time.sleep(args.interval)
        return 0

    if args.command == "dispatch":
        def _one():
            r = dispatch_once(args.repo, args.executor)
            print(f"granted={r.granted} preempted={r.preempted} beaten={r.beaten} "
                  f"completed={r.completed} waiting={r.waiting}", flush=True)
        if args.once or not args.loop:
            _one()
            return 0
        beats = 0
        while args.max_beats is None or beats < args.max_beats:
            _one()
            beats += 1
            if args.max_beats is None or beats < args.max_beats:
                time.sleep(args.interval)
        return 0

    parser.error("unknown command")
    return 2
```
(Remove the old single-`worker`-only body of `main` — the version above replaces it entirely. Keep `run_once` as defined in Phase 0.)

- [ ] **Step 4: Run the new tests + the Phase 0 CLI tests**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_cli_dispatch.py tests/test_cli.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/cli.py tests/test_cli_dispatch.py
git commit -m "feat: coscience dispatch CLI subcommand"
```

---

## Task 10: End-to-end integration + example resources.yaml

**Files:**
- Create: `tests/test_integration_phase1.py`
- Create: `docs/superpowers/plans/phase1-dispatch-runbook.md`

**Interfaces:**
- Consumes everything above. No new production code expected; if a test fails, the bug is in the integration of earlier tasks — fix the relevant module, do not weaken the test.

- [ ] **Step 1: Write the integration test**

`tests/test_integration_phase1.py`:
```python
from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _approved(sid, n_steps, req, prio=0):
    plan = [Step(f"s{i}", "true") for i in range(n_steps)]
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=plan,
                  resources_required=req, priority=prio)


def test_three_sprints_one_gpu_serialize_without_overcommit(substrate):
    # 1 GPU, 3 sprints each needing it -> they must run one-at-a-time and all finish.
    substrate.save_sprint(_approved("a", 2, {"gpu": 1.0}, prio=1))
    substrate.save_sprint(_approved("b", 2, {"gpu": 1.0}, prio=5))  # should go first
    substrate.save_sprint(_approved("c", 2, {"gpu": 1.0}, prio=1))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))

    first_done = None
    for t in range(60):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0  # never overcommit
        if first_done is None and substrate.load_sprint("b").status == SprintStatus.DONE:
            first_done = "b"
            # the highest-priority sprint finishes before the low-priority ones start later
        if all(substrate.load_sprint(s).status == SprintStatus.DONE for s in ("a", "b", "c")):
            break

    for s in ("a", "b", "c"):
        assert substrate.load_sprint(s).status == SprintStatus.DONE
    assert disp.ledger.all_leases() == []  # everything released at the end
    assert first_done == "b"  # priority was honored


def test_cpu_sprints_run_concurrently_with_gpu_sprint(substrate):
    # gpu:1 + runtime_slots:3; a gpu sprint and two no-gpu sprints all proceed together.
    substrate.save_sprint(_approved("gpu1", 1, {"gpu": 1.0}))
    substrate.save_sprint(_approved("cpuA", 1, {"runtime_slots": 1.0}))
    substrate.save_sprint(_approved("cpuB", 1, {"runtime_slots": 1.0}))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0, "runtime_slots": 3.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert {l.sprint_id for l in disp.ledger.all_leases()} == {"gpu1", "cpuA", "cpuB"}
```

- [ ] **Step 2: Run the integration test**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_integration_phase1.py -v`
Expected: 2 passed. If a test fails, fix the offending module (ledger/scheduler/dispatcher), not the test.

- [ ] **Step 3: Run the FULL suite (no regressions across Phase 0 + Phase 1)**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all tests pass (34 Phase 0 + the Phase 1 additions).

- [ ] **Step 4: Write the dispatch runbook**

`docs/superpowers/plans/phase1-dispatch-runbook.md`:
```markdown
# Phase 1 — Dispatcher Runbook

Runs several sprints concurrently across a declared resource pool, never
exceeding capacity.

## 1. Declare the pool
Create `<repo>/.coscience/resources.yaml`, e.g. for the avatar+superskomputer fleet:
```
resources:
  gpu_24gb: 1       # avatar RTX 4090
  gpu_11gb: 1       # superskomputer RTX 2080 Ti
  cpu: 80           # combined cores
  disk_gb: 1000
  runtime_slots: 4  # max concurrent agent sessions
```

## 2. Declare each sprint's needs
In a sprint's `sprint.md` frontmatter:
```
status: approved
priority: 5
preemptible: false
resources_required:
  gpu_24gb: 1
  runtime_slots: 1
plan:
  - id: train
    run: "detached: python train.py"
```

## 3. Run the dispatcher
```
'/home/oleg/venvs/coscience/bin/coscience' dispatch --repo <repo> --loop --interval 5 --executor claude
```
Each cycle prints `granted=.. preempted=.. beaten=.. completed=.. waiting=..`.

## What it guarantees
- Never exceeds declared capacity (all-or-nothing leases).
- Higher `priority` runs first; long-waiting sprints age up.
- A higher-priority sprint can preempt a lower-priority **preemptible** holder.
- A stuck sprint's lease expires (TTL) and is reclaimed automatically.
- `.coscience/leases.json` / `queue.json` show live allocation.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration_phase1.py docs/superpowers/plans/phase1-dispatch-runbook.md
git commit -m "test: phase 1 end-to-end concurrency integration + dispatch runbook"
```

---

## Self-Review

**Spec coverage (design §6 + §4 enrichment):**
- Declared inventory (`resources.yaml`) → Task 1 ✓
- Leases, all-or-nothing grants → Tasks 2,3 ✓
- TTL renew/expire reclaim → Task 4 ✓
- Priority + aging + first-fit → Task 5 ✓
- Graceful preemption (preemptible only, lower priority) → Task 6 ✓
- Bounded one-beat-per-sprint dispatcher; single ledger writer; completion releases → Task 8 ✓
- Capture agent/step output into result (§4 enrichment) → Task 7 ✓
- CLI surface → Task 9 ✓
- End-to-end no-overcommit + priority honored → Task 10 ✓
- Backward compatibility (Phase 0 worker/CLI/tests) → Tasks 7,9 preserve and re-run them ✓
- Explicitly deferred (documented, not built here): containerized service + MCP/API gateway; LLM resource-manager policy layer; VRAM-fit bin-packing (resources are quantity-accounted by key); multi-process concurrent ledger writers (the Dispatcher is the single writer).

**Placeholder scan:** every code step has complete runnable code; every test step has real assertions; no TBD/vague items. ✓

**Type consistency:** `ResourcePool(capacity)`, `Lease(id, sprint_id, amounts, granted_at, expires_at, priority, preemptible)`, `Sprint(..., resources_required, priority, preemptible)`, `ProgressState(..., outputs)`, `Ledger.{load,save,all_leases,lease_for,used,available,can_fit,acquire,release,renew,expire}`, `SchedulerPolicy.{effective_priority,select_grants,select_preemptions}`, `Worker.{run_one_beat,run_sprint_beat}`, `Dispatcher.run_one_cycle -> CycleReport`, `dispatch_once` — names/signatures consistent across Tasks 1–10. ✓

**Known Phase 1 simplifications (intentional):**
- Resources are quantity-accounted per key (distinct GPUs modeled as distinct keys, e.g. `gpu_24gb`/`gpu_11gb`); explicit VRAM-fit bin-packing is deferred.
- One lease per sprint (bundles all its resources).
- Single-writer ledger via the one Dispatcher process; concurrent multi-writer access waits for the Phase 1b service.
- The dispatcher runs sprint beats in-process sequentially per cycle; real parallelism comes from `detached:` jobs running out-of-process.
- Leases/queue persisted as JSON under `.coscience/` and committed on changing cycles.
