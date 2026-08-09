# Worker Concurrency Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound how many worker agents run at once by modelling a worker slot as a pooled resource, and let that pool be edited from the dashboard's Compute page.

**Architecture:** `.coscience/resources.yaml` may declare a `workers:` capacity. When it does, every sprint's effective resource requirement gains `workers: 1`, so the existing ledger — priority, aging, all-or-nothing grants, cooperative yield — enforces the ceiling with no new scheduling concept. A pool with no `workers` key behaves exactly as today. A new `PUT /api/capacity` writes the YAML atomically and commits it; `Service.pool` already re-reads the file per access, so edits take effect with no restart.

**Tech Stack:** Python 3.12, FastAPI, pydantic, PyYAML, pytest. React 18 + TypeScript, Mantine, TanStack Query, Vitest + Testing Library.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-08-08-worker-concurrency-limits-design.md`.
- Branch: `feat/worker-concurrency-limits`. Never push; never commit outside this branch.
- The resource key is the literal string `workers`, exported as `WORKER_KEY` from `src/coscience/resources.py`. Never hardcode `"workers"` anywhere else in Python.
- A pool **without** a `workers` key must behave exactly as it does today. `tests/test_scheduler_grants.py::test_no_resource_sprints_always_granted` is the standing regression guard and must keep passing unmodified.
- `workers: 0` is valid and means "no new grants"; it is never special-cased.
- Capacity values are floats `>= 0`, finite. Negative, NaN and infinity are rejected.
- Lowering capacity below current usage **drains**: never hibernate, never kill, never refuse the edit.
- The PM loop is out of scope and must not be touched. It takes no lease.
- Run Python tests with `~/venvs/coscience/bin/python -m pytest`. Run frontend tests with `npm test` from `frontend/`.
- Existing code style: no docstring on every function, but a short one where the *why* is non-obvious; comments explain rationale, not mechanics.

---

### Task 1: The effective requirement

A pure function that answers "what does this sprint actually consume?". Everything else in the plan depends on it.

**Files:**
- Modify: `src/coscience/resources.py`
- Test: `tests/test_resources.py`

**Interfaces:**
- Consumes: `ResourcePool` (already in this module).
- Produces: `WORKER_KEY: str = "workers"` and
  `effective_requirement(required: dict[str, float], pool: ResourcePool) -> dict[str, float]`.
  Tasks 2 and 7 import both from `coscience.resources`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resources.py`:

```python
from coscience.resources import WORKER_KEY, effective_requirement


def test_effective_requirement_is_unchanged_without_a_worker_cap():
    pool = ResourcePool({"cpu": 8.0})
    assert effective_requirement({"cpu": 2.0}, pool) == {"cpu": 2.0}


def test_effective_requirement_adds_a_worker_slot_when_capped():
    pool = ResourcePool({"cpu": 8.0, WORKER_KEY: 2.0})
    assert effective_requirement({"cpu": 2.0}, pool) == {"cpu": 2.0, WORKER_KEY: 1.0}


def test_effective_requirement_bounds_a_sprint_declaring_nothing():
    pool = ResourcePool({WORKER_KEY: 1.0})
    assert effective_requirement({}, pool) == {WORKER_KEY: 1.0}


def test_effective_requirement_does_not_mutate_its_input():
    pool = ResourcePool({WORKER_KEY: 1.0})
    required = {"cpu": 1.0}
    effective_requirement(required, pool)
    assert required == {"cpu": 1.0}


def test_effective_requirement_ignores_a_self_declared_worker_amount():
    # A sprint may not buy itself extra slots; one agent is one slot.
    pool = ResourcePool({WORKER_KEY: 4.0})
    assert effective_requirement({WORKER_KEY: 3.0}, pool) == {WORKER_KEY: 1.0}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_resources.py -v`
Expected: FAIL — `ImportError: cannot import name 'WORKER_KEY' from 'coscience.resources'`

- [ ] **Step 3: Implement**

Append to `src/coscience/resources.py`:

```python
WORKER_KEY = "workers"


def effective_requirement(required: dict[str, float], pool: ResourcePool) -> dict[str, float]:
    """What a sprint actually consumes. When the pool declares a worker cap, every
    sprint costs one worker slot on top of what it declares — that is what bounds
    the number of agent processes running at once. A pool with no `workers` key is
    uncapped, exactly as before."""
    if WORKER_KEY not in pool.capacity:
        return dict(required)
    return {**required, WORKER_KEY: 1.0}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_resources.py -v`
Expected: PASS (10 tests — 5 existing, 5 new)

- [ ] **Step 5: Commit**

```bash
git add src/coscience/resources.py tests/test_resources.py
git commit -m "feat(resources): a sprint costs one worker slot when the pool caps workers"
```

---

### Task 2: Enforce the slot in the scheduling path

The scheduler and the dispatcher both decide whether a sprint fits. They must use the *same* notion of what it costs. If only `acquire` injected the slot, `select_grants` would over-select, `acquire` would return `None` for the excess, and those sprints would fail to be granted without `report.granted` ever reflecting it — a silent undercount instead of a visible limit. Both change together, in one task, for that reason.

**Files:**
- Modify: `src/coscience/scheduler.py` (`select_grants`, `select_yield_victims`)
- Modify: `src/coscience/dispatcher.py` (the `ledger.acquire` call, currently line 79)
- Test: `tests/test_scheduler_workers.py` (create)

**Interfaces:**
- Consumes: `WORKER_KEY`, `effective_requirement` from Task 1.
- Produces: no new public names. After this task a pool with `workers: N` grants at most N sprints at a time.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler_workers.py`:

```python
"""The worker cap: `workers: N` in the pool bounds how many sprints hold leases."""
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import WORKER_KEY, ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio)


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_worker_cap_bounds_grants(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"cpu": 16.0, WORKER_KEY: 2.0})
    sprints = [_sprint(f"s{i}", req={"cpu": 1.0}) for i in range(5)]
    q = {s.id: 0.0 for s in sprints}
    granted = pol.select_grants(sprints, q, led, now=0.0)
    assert len(granted) == 2


def test_worker_cap_bounds_sprints_declaring_no_resources(tmp_path):
    # The hole this feature closes: all() over an empty dict is vacuously true,
    # so uncapped these would all be granted.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 1.0})
    sprints = [_sprint(f"s{i}", req={}) for i in range(4)]
    q = {s.id: 0.0 for s in sprints}
    granted = pol.select_grants(sprints, q, led, now=0.0)
    assert len(granted) == 1


def test_worker_cap_grants_by_priority(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 1.0})
    lo = _sprint("lo", prio=0)
    hi = _sprint("hi", prio=5)
    granted = pol.select_grants([lo, hi], {"lo": 0.0, "hi": 0.0}, led, now=0.0)
    assert [s.id for s in granted] == ["hi"]


def test_worker_cap_of_zero_grants_nothing(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"cpu": 16.0, WORKER_KEY: 0.0})
    granted = pol.select_grants([_sprint("s1", req={"cpu": 1.0})], {"s1": 0.0}, led, now=0.0)
    assert granted == []


def test_existing_leases_consume_the_cap(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 2.0})
    led.acquire("running", {WORKER_KEY: 1.0}, now=0.0, ttl=60.0)
    sprints = [_sprint("a"), _sprint("b")]
    granted = pol.select_grants(sprints, {"a": 0.0, "b": 0.0}, led, now=0.0)
    assert len(granted) == 1


def test_yield_victims_account_for_the_worker_slot(tmp_path):
    # The candidate needs a worker slot it can only get by preempting a holder.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 1.0})
    led.acquire("holder", {WORKER_KEY: 1.0}, now=0.0, ttl=60.0, priority=0)
    cand = _sprint("cand", prio=5)
    victims = pol.select_yield_victims(cand, 5, led, yieldable_ids={"holder"})
    assert [v.sprint_id for v in victims] == ["holder"]


def test_uncapped_pool_still_grants_everything(tmp_path):
    # Regression guard: no `workers` key means today's behaviour, unchanged.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"cpu": 2.0})
    sprints = [_sprint(f"s{i}", req={}) for i in range(6)]
    q = {s.id: 0.0 for s in sprints}
    assert len(pol.select_grants(sprints, q, led, now=0.0)) == 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_scheduler_workers.py -v`
Expected: FAIL — `test_worker_cap_bounds_grants` asserts 2 but gets 5; the cap is not enforced yet.

- [ ] **Step 3: Implement in `scheduler.py`**

Add the import at the top of `src/coscience/scheduler.py`:

```python
from coscience.resources import effective_requirement
```

Replace the body of `select_grants` (currently lines 20-33) with:

```python
    def select_grants(self, candidates, queued_at, ledger: Ledger, now) -> list[Sprint]:
        avail = dict(ledger.available())

        def sort_key(s: Sprint):
            return (-self.effective_priority(s, queued_at.get(s.id, now), now),
                    queued_at.get(s.id, now))

        granted: list[Sprint] = []
        for sprint in sorted(candidates, key=sort_key):
            need = effective_requirement(sprint.resources_required, ledger.pool)
            if all(avail.get(k, 0.0) >= v for k, v in need.items()):
                for k, v in need.items():
                    avail[k] = avail.get(k, 0.0) - v
                granted.append(sprint)
        return granted
```

In `select_yield_victims`, replace the single line `need = candidate.resources_required` (currently line 43) with:

```python
        need = effective_requirement(candidate.resources_required, ledger.pool)
```

- [ ] **Step 4: Implement in `dispatcher.py`**

Change the import line `from coscience.resources import ResourcePool` to:

```python
from coscience.resources import ResourcePool, effective_requirement
```

Replace the `ledger.acquire` call (currently lines 79-80):

```python
            if self.ledger.acquire(sprint.id,
                                   effective_requirement(sprint.resources_required,
                                                         self.ledger.pool),
                                   now, ttl,
                                   priority=eff, preemptible=sprint.preemptible):
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_scheduler_workers.py -v`
Expected: PASS (7 tests)

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS, no regressions. `tests/test_scheduler_grants.py::test_no_resource_sprints_always_granted` must still pass unmodified — its pool has no `workers` key.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/scheduler.py src/coscience/dispatcher.py tests/test_scheduler_workers.py
git commit -m "feat(scheduler): enforce the worker cap when granting and when yielding"
```

---

### Task 3: The dispatcher honours the cap end to end

Task 2 proved the policy. This proves the whole cycle: with `workers: 1`, one full `run_one_cycle` leases exactly one sprint and launches exactly one agent.

**Files:**
- Test: `tests/test_dispatcher_workers.py` (create)

**Interfaces:**
- Consumes: `Dispatcher`, `WORKER_KEY`, the `substrate` fixture from `tests/conftest.py`, and `FakeAgent` from `tests.conftest` — the same harness `tests/test_dispatcher.py` uses. Do not write a second, divergent one.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatcher_workers.py`:

```python
"""End to end: a worker cap bounds how many sprints a real cycle leases."""
from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import WORKER_KEY, ResourcePool
from coscience.scheduler import SchedulerPolicy


def _queued(sid, req=None, prio=0):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["do the work"],
                  resources_required=req or {}, priority=prio)


def _dispatcher(substrate, capacity):
    return Dispatcher(substrate, FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def test_one_cycle_grants_only_one_sprint_under_a_worker_cap(substrate):
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid, req={"cpu": 1.0}))
    disp = _dispatcher(substrate, {"cpu": 16.0, WORKER_KEY: 1.0})
    report = disp.run_one_cycle(now=0.0)
    assert report.granted == 1
    assert report.waiting == 2


def test_sprints_declaring_nothing_are_bounded_too(substrate):
    # Without a worker cap these are all granted: all() over {} is vacuously true.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = _dispatcher(substrate, {WORKER_KEY: 1.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert len(disp.ledger.all_leases()) == 1


def test_the_cap_holds_across_cycles(substrate):
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = _dispatcher(substrate, {WORKER_KEY: 1.0})
    for t in range(30):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get(WORKER_KEY, 0.0) <= 1.0


def test_all_sprints_still_finish_under_a_cap_of_one(substrate):
    # Serialised, not starved: the cap orders work, it doesn't drop it.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = _dispatcher(substrate, {WORKER_KEY: 1.0})
    for t in range(60):
        disp.run_one_cycle(now=float(t))
    for sid in ("a", "b", "c"):
        assert substrate.load_sprint(sid).status == SprintStatus.DONE


def test_two_workers_run_two_sprints_at_once(substrate):
    substrate.save_sprint(_queued("a"))
    substrate.save_sprint(_queued("b"))
    disp = _dispatcher(substrate, {WORKER_KEY: 2.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("a") is not None
    assert disp.ledger.lease_for("b") is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_dispatcher_workers.py -v`

If they already pass, that is the expected outcome — Task 2 implemented the behaviour and this task is its integration proof. Confirm the tests genuinely bind by temporarily changing the first test's capacity to `{"cpu": 16.0, WORKER_KEY: 3.0}`: it must then FAIL with `granted == 3`. Change it back.

`test_all_sprints_still_finish_under_a_cap_of_one` needs enough cycles for three sprints to run in series; if 60 proves too few with `FakeAgent`, raise the count rather than weakening the assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispatcher_workers.py
git commit -m "test(dispatcher): a worker cap of 1 leases one sprint per cycle"
```

---

### Task 4: `Service.set_capacity`

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_capacity.py` (create)

**Interfaces:**
- Produces: `Service.set_capacity(capacity: dict) -> dict`. Returns the same shape as `ledger_status()`. Raises `ValueError` on invalid input. Task 5 (HTTP) calls it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_service_capacity.py`:

```python
import pytest
import yaml

from coscience.service import Service


def test_set_capacity_writes_the_pool_file(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"cpu": 16, "gpu": 1, "workers": 1})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written == {"cpu": 16.0, "gpu": 1.0, "workers": 1.0}


def test_set_capacity_returns_fresh_ledger_status(tmp_path):
    svc = Service(tmp_path)
    status = svc.set_capacity({"workers": 2})
    assert status["capacity"] == {"workers": 2.0}
    assert status["available"] == {"workers": 2.0}


def test_set_capacity_is_visible_without_restart(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"workers": 3})
    assert svc.pool.capacity == {"workers": 3.0}


def test_set_capacity_accepts_zero(tmp_path):
    svc = Service(tmp_path)
    assert svc.set_capacity({"workers": 0})["capacity"] == {"workers": 0.0}


def test_set_capacity_can_drop_below_current_usage(tmp_path):
    # Draining is legal: the edit is accepted even while more is leased than allowed.
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    led = Ledger(ResourcePool({"workers": 2.0}), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"workers": 1.0}, now=0.0, ttl=60.0)
    led.acquire("sp2", {"workers": 1.0}, now=0.0, ttl=60.0)

    svc = Service(tmp_path)
    status = svc.set_capacity({"workers": 1})
    assert status["capacity"] == {"workers": 1.0}
    assert status["available"] == {"workers": -1.0}     # over-committed, draining
    assert len(status["leases"]) == 2                   # nothing was killed


def test_set_capacity_empties_the_pool(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"cpu": 4})
    assert svc.set_capacity({})["capacity"] == {}


@pytest.mark.parametrize("bad", [
    {"cpu": -1},
    {"cpu": float("nan")},
    {"cpu": float("inf")},
    {"cpu": "eight"},
    {"cpu": None},
    {"cpu": True},
    {"": 1},
    {"  ": 1},
    {"resources": 1},
])
def test_set_capacity_rejects_bad_input(tmp_path, bad):
    with pytest.raises(ValueError):
        Service(tmp_path).set_capacity(bad)


def test_set_capacity_rejects_duplicate_names_after_trimming(tmp_path):
    with pytest.raises(ValueError):
        Service(tmp_path).set_capacity({"cpu": 1, " cpu ": 2})


def test_rejected_input_leaves_the_file_untouched(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"cpu": 4})
    with pytest.raises(ValueError):
        svc.set_capacity({"cpu": -1})
    assert svc.pool.capacity == {"cpu": 4.0}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_capacity.py -v`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'set_capacity'`

- [ ] **Step 3: Implement**

`src/coscience/service.py` already imports `Path` and `yaml` is used elsewhere in the package; add whatever of `import math`, `import os`, `import yaml` is missing at the top, following the existing import ordering.

Add the method next to `ledger_status` (around line 1404):

```python
    def set_capacity(self, capacity: dict) -> dict:
        """Replace the declared resource pool. Validates, writes
        .coscience/resources.yaml atomically, commits, and returns fresh ledger
        status. Lowering a limit below what is currently leased is allowed and
        drains: running work keeps its lease, new grants stop."""
        clean: dict[str, float] = {}
        for raw_key, raw_val in (capacity or {}).items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("a resource needs a name")
            if key == "resources":
                # ResourcePool.from_dict treats a top-level `resources:` mapping as
                # the wrapper, so a resource actually named that would vanish.
                raise ValueError("'resources' is reserved and can't be a resource name")
            if key in clean:
                raise ValueError(f"duplicate resource name: {key}")
            if isinstance(raw_val, bool) or not isinstance(raw_val, (int, float)):
                raise ValueError(f"{key}: capacity must be a number")
            val = float(raw_val)
            if not math.isfinite(val):
                raise ValueError(f"{key}: capacity must be a finite number")
            if val < 0:
                raise ValueError(f"{key}: capacity can't be negative")
            clean[key] = val

        path = self.repo_root / ".coscience" / "resources.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(yaml.safe_dump(clean, sort_keys=True))
        os.replace(tmp, path)      # atomic: a dispatcher reading it never sees a partial file
        self.substrate.commit("capacity updated")
        return self.ledger_status()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_capacity.py -v`
Expected: PASS (17 tests, counting the parametrised cases)

Note: these tests construct `Service(tmp_path)` with no injected pool. A `Service` built with `pool=` uses that override for `self.pool`, so `set_capacity` would write the file while `ledger_status` reported the override. Don't inject a pool in these tests.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_capacity.py
git commit -m "feat(service): set_capacity writes and commits the resource pool"
```

---

### Task 5: `PUT /api/capacity`

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_capacity.py` (create)

**Interfaces:**
- Consumes: `Service.set_capacity` from Task 4.
- Produces: `PUT /api/capacity`, body `{"capacity": {"<name>": <number>}}`, returns the `ledger_status()` shape. Task 6's `api.setCapacity` calls it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_http_capacity.py`:

```python
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    return TestClient(build_app(Service(tmp_path)))


def test_put_capacity_updates_and_returns_ledger_status(client):
    r = client.put("/api/capacity", json={"capacity": {"cpu": 16, "workers": 1}})
    assert r.status_code == 200
    assert r.json()["capacity"] == {"cpu": 16.0, "workers": 1.0}


def test_put_capacity_is_reflected_by_get_ledger(client):
    client.put("/api/capacity", json={"capacity": {"workers": 2}})
    assert client.get("/api/ledger").json()["capacity"] == {"workers": 2.0}


def test_put_capacity_rejects_a_negative_value(client):
    r = client.put("/api/capacity", json={"capacity": {"cpu": -1}})
    assert r.status_code == 422


def test_put_capacity_rejects_a_blank_name(client):
    r = client.put("/api/capacity", json={"capacity": {"": 1}})
    assert r.status_code == 422


def test_put_capacity_accepts_an_empty_pool(client):
    r = client.put("/api/capacity", json={"capacity": {}})
    assert r.status_code == 200
    assert r.json()["capacity"] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_capacity.py -v`
Expected: FAIL — 405 Method Not Allowed (the route doesn't exist)

- [ ] **Step 3: Implement**

Add the request model beside the other pydantic models near the top of `src/coscience/http_api.py` (they live around lines 50-90):

```python
class CapacityUpdate(BaseModel):
    capacity: dict[str, float] = Field(default_factory=dict)
```

Add the route immediately after the existing `ledger_status` route (currently lines 657-659):

```python
    @api.put("/capacity")
    def set_capacity(body: CapacityUpdate) -> dict:
        try:
            return service.set_capacity(body.capacity)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

Pydantic's `dict[str, float]` coerces or rejects non-numeric values before the service sees them, so the "must be a number" branch of `set_capacity` is unreachable over HTTP. It stays because `set_capacity` is also called directly (and is covered by Task 4's tests).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_capacity.py -v`
Expected: PASS (5 tests)

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_capacity.py
git commit -m "feat(http): PUT /api/capacity edits the resource pool"
```

---

### Task 6: The capacity modal

**Files:**
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/components/CapacityModal.tsx`
- Test: `frontend/src/components/CapacityModal.test.tsx` (create)

**Interfaces:**
- Consumes: `PUT /api/capacity` from Task 5; the `Ledger` interface already in `api.ts`.
- Produces: `api.setCapacity(capacity: Record<string, number>): Promise<Ledger>` and a default-exported
  `CapacityModal({ opened, onClose, capacity, used }: { opened: boolean; onClose: () => void; capacity: Record<string, number>; used: Record<string, number> })`.
  Task 7 renders it.

- [ ] **Step 1: Add the API client method**

In `frontend/src/api.ts`, directly after the `getLedger` entry:

```ts
  setCapacity: (capacity: Record<string, number>) =>
    fetch("/api/capacity", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capacity }),
    }).then(j<Ledger>),
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/components/CapacityModal.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: { setCapacity: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [] }) },
}));

import { api } from "../api";
import CapacityModal from "./CapacityModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderModal(capacity = { cpu: 16, workers: 1 }, used = { cpu: 2, workers: 1 }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <CapacityModal opened onClose={() => {}} capacity={capacity} used={used} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("CapacityModal", () => {
  it("shows a row per resource, pre-filled with current capacity", () => {
    renderModal();
    expect((screen.getByLabelText("cpu capacity") as HTMLInputElement).value).toBe("16");
    expect((screen.getByLabelText("workers capacity") as HTMLInputElement).value).toBe("1");
  });

  it("saves the edited map", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("workers capacity"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(api.setCapacity).toHaveBeenCalledWith({ cpu: 16, workers: 3 }));
  });

  it("removes a resource", async () => {
    renderModal();
    fireEvent.click(screen.getByLabelText("remove cpu"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(api.setCapacity).toHaveBeenCalledWith({ workers: 1 }));
  });

  it("adds a resource", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /add resource/i }));
    fireEvent.change(screen.getByLabelText("new resource name"), { target: { value: "gpu" } });
    fireEvent.change(screen.getByLabelText("new resource capacity"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(api.setCapacity).toHaveBeenCalledWith({ cpu: 16, workers: 1, gpu: 1 }));
  });

  it("refuses a negative value without calling the API", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("cpu capacity"), { target: { value: "-2" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText(/zero or more/i)).toBeTruthy());
    expect(api.setCapacity).not.toHaveBeenCalled();
  });

  it("warns when a new limit is below what is in use", () => {
    renderModal({ cpu: 16 }, { cpu: 14 });
    fireEvent.change(screen.getByLabelText("cpu capacity"), { target: { value: "4" } });
    expect(screen.getByText(/14 cpu in use/i)).toBeTruthy();
    expect(screen.getByText(/running work finish/i)).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run from `frontend/`: `npm test -- CapacityModal`
Expected: FAIL — cannot resolve `./CapacityModal`

- [ ] **Step 4: Implement the component**

Create `frontend/src/components/CapacityModal.tsx`:

```tsx
import { Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";

interface Props {
  opened: boolean;
  onClose: () => void;
  capacity: Record<string, number>;
  used: Record<string, number>;
}

interface Row { key: string; value: string }

export default function CapacityModal({ opened, onClose, capacity, used }: Props) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<Row[]>([]);
  const [adding, setAdding] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [error, setError] = useState("");

  // Re-seed from the server every time the modal opens, so a stale local edit
  // can't overwrite a change made elsewhere.
  useEffect(() => {
    if (!opened) return;
    setRows(Object.entries(capacity).map(([key, v]) => ({ key, value: String(v) })));
    setAdding(false); setNewKey(""); setNewValue(""); setError("");
  }, [opened, capacity]);

  const collect = (): Record<string, number> | null => {
    const out: Record<string, number> = {};
    const entries = [...rows, ...(adding && newKey.trim() ? [{ key: newKey, value: newValue }] : [])];
    for (const row of entries) {
      const key = row.key.trim();
      if (!key) { setError("Every resource needs a name."); return null; }
      if (key in out) { setError(`Two resources are both called "${key}".`); return null; }
      const n = Number(row.value);
      if (row.value.trim() === "" || !Number.isFinite(n) || n < 0) {
        setError(`${key}: capacity must be zero or more.`); return null;
      }
      out[key] = n;
    }
    return out;
  };

  // Limits being lowered under what's already leased — worth saying out loud,
  // because the answer (drain, don't kill) isn't obvious.
  const shrinking = rows.filter((r) => (used[r.key] ?? 0) > Number(r.value));

  const save = async () => {
    setError("");
    const payload = collect();
    if (!payload) return;
    try {
      await api.setCapacity(payload);
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Edit capacity">
      <Stack>
        {rows.map((row, i) => (
          <Group key={row.key} gap="xs" wrap="nowrap">
            <TextInput label={`${row.key} capacity`} style={{ flex: 1 }}
                       value={row.value}
                       onChange={(e) => setRows(rows.map((r, j) =>
                         j === i ? { ...r, value: e.currentTarget.value } : r))} />
            <Button variant="subtle" color="gray" aria-label={`remove ${row.key}`}
                    style={{ alignSelf: "flex-end" }}
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</Button>
          </Group>
        ))}

        {adding ? (
          <Group gap="xs" wrap="nowrap">
            <TextInput label="new resource name" style={{ flex: 1 }} value={newKey}
                       onChange={(e) => setNewKey(e.currentTarget.value)} />
            <TextInput label="new resource capacity" style={{ width: 120 }} value={newValue}
                       onChange={(e) => setNewValue(e.currentTarget.value)} />
          </Group>
        ) : (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  onClick={() => setAdding(true)}>
            + add resource
          </button>
        )}

        {shrinking.map((r) => (
          <Text key={r.key} size="sm" c="dimmed">
            {used[r.key]} {r.key} in use — lowering below that lets running work finish
            and blocks new grants.
          </Text>
        ))}

        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={save}>Save</Button>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run from `frontend/`: `npm test -- CapacityModal`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/CapacityModal.tsx frontend/src/components/CapacityModal.test.tsx
git commit -m "feat(frontend): a modal for editing the resource pool"
```

---

### Task 7: Wire it into the Compute page

**Files:**
- Modify: `frontend/src/views/Ledger.tsx`
- Test: `frontend/src/views/Ledger.test.tsx` (create)

**Interfaces:**
- Consumes: `CapacityModal` from Task 6; `WORKER_KEY`'s literal value `"workers"` — in TypeScript, define it once at the top of `Ledger.tsx` as `const WORKER_KEY = "workers";` and use that constant.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/views/Ledger.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

const ledger = vi.fn();
vi.mock("../api", () => ({
  api: {
    getLedger: () => ledger(),
    getUsage: () => Promise.resolve(null),
    setCapacity: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [] }),
  },
}));

import Ledger from "./Ledger";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter><Ledger /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("Compute page", () => {
  it("warns that agents are unbounded when no worker cap is set", async () => {
    ledger.mockResolvedValue({ capacity: { cpu: 16 }, used: { cpu: 2 }, available: { cpu: 14 }, leases: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no worker cap/i)).toBeTruthy());
  });

  it("does not warn once a worker cap exists", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: { cpu: 2, workers: 1 },
      available: { cpu: 14, workers: 0 }, leases: [],
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/capacity in use/i)).toBeTruthy());
    expect(screen.queryByText(/no worker cap/i)).toBeNull();
  });

  it("opens the edit modal", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: {}, available: {}, leases: [],
    });
    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /edit capacity/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /edit capacity/i }));
    await waitFor(() => expect(screen.getByLabelText("workers capacity")).toBeTruthy());
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `frontend/`: `npm test -- Ledger`
Expected: FAIL — no "no worker cap" text, no "edit capacity" button.

- [ ] **Step 3: Implement**

In `frontend/src/views/Ledger.tsx`: import `useState` from `react`, `Button` and `Group` from `@mantine/core`, and `CapacityModal from "../components/CapacityModal"`. Add `const WORKER_KEY = "workers";` beside the existing `cardStyle` constant, and `const [editing, setEditing] = useState(false);` beside the queries.

Replace the "capacity in use" `Card` with:

```tsx
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" style={{ marginBottom: 16 }}>
          <div className="eyebrow">capacity in use</div>
          <Button size="xs" variant="default" onClick={() => setEditing(true)}>Edit capacity</Button>
        </Group>

        {!(WORKER_KEY in l.capacity) && (
          <Text size="sm" c="dimmed" style={{ marginBottom: 16 }}>
            No worker cap — any number of agents can run at once. Add a{" "}
            <code>{WORKER_KEY}</code> limit to bound it.
          </Text>
        )}

        {keys.length ? (
          <Stack gap={16}>
            {keys.map((k) => <Gauge key={k} label={k} used={l.used[k] ?? 0} capacity={l.capacity[k]} />)}
          </Stack>
        ) : <Text size="sm" c="dimmed">No compute pool is configured yet, so there's nothing to meter.</Text>}
      </Card>

      <CapacityModal opened={editing} onClose={() => setEditing(false)}
                     capacity={l.capacity} used={l.used} />
```

Place the `<CapacityModal …/>` as the last child of the outer `<Stack>`, after the "running now" card.

- [ ] **Step 4: Run the tests to verify they pass**

Run from `frontend/`: `npm test -- Ledger`
Expected: PASS (3 tests)

Run from `frontend/`: `npm test`
Expected: PASS, whole frontend suite green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Ledger.tsx frontend/src/views/Ledger.test.tsx
git commit -m "feat(frontend): edit capacity from the Compute page, flag a missing worker cap"
```

---

### Task 8: Set Avatar's limits and verify against the running platform

Code only; the substrate edit is a **deployment action** on a separate git repo (`~/sync/bmt-share/coscience`). Do not commit substrate changes to the code repo.

**Files:**
- Modify: `~/sync/bmt-share/coscience/.coscience/resources.yaml` (substrate, not this repo)

- [ ] **Step 1: Confirm the full suite is green**

Run: `~/venvs/coscience/bin/python -m pytest`
Run from `frontend/`: `npm test`
Expected: both PASS. Do not proceed otherwise.

- [ ] **Step 2: Deploy the branch to the running platform**

`scripts/deploy.sh` does not work on Avatar (it calls `pip`, and this venv has only `pip3`). Use the steps from `local_setup_avatar.md`, from a **script file, not `bash -c`** — the `pgrep -f` patterns match an inline wrapper's own command line and it kills itself. Build the frontend (`cd frontend && npm run build`) — always, per CLAUDE.md rule 1 — then restart `coscience-http` and both loops with `setsid`, against `SUB=$HOME/sync/bmt-share/coscience`.

- [ ] **Step 3: Set the limits through the UI, not the file**

Open http://127.0.0.1:8000/ledger, hard-reload (Ctrl-Shift-R) to drop the cached bundle. Confirm the "No worker cap" callout is showing. Click **Edit capacity**, set:

```
cpu      16     (unchanged)
gpu       1     (was 4 — the box has one RTX 4090)
workers   1     (new)
```

Save. This exercises the real write path end to end rather than testing it by hand-editing YAML.

- [ ] **Step 4: Verify**

```bash
cat ~/sync/bmt-share/coscience/.coscience/resources.yaml
curl -s 127.0.0.1:8000/api/ledger
git -C ~/sync/bmt-share/coscience log --oneline -1
```

Expected: the file reads `cpu: 16.0`, `gpu: 1.0`, `workers: 1.0`; the API reports the same capacity; the substrate's last commit is `capacity updated`. The callout is gone and a `workers` gauge is rendering.

Then confirm the cap binds: with 14 `proposed` sprints in the pool, release two approved sprints and watch `~/coscience-dispatch.log`. Expected: `granted 1` and the second sprint waiting, not two concurrent agents.

- [ ] **Step 5: Note the substrate commit**

`Substrate.commit` runs `git add -A`, so the "capacity updated" commit also sweeps in whatever else was dirty in the substrate. Report what it captured rather than letting it pass silently.

---

## Notes for the implementer

**Do not touch the PM.** `coscience pm --loop` is a separate process that never takes a lease. The cap governs worker agents only, by design.

**A sleeping sprint holds its slot.** When a worker declares a detached job and sleeps on it (`job_token` set, no agent), it keeps its lease and therefore its worker slot for the job's whole duration. Under `workers: 1` a multi-hour job blocks every other sprint. This is a known consequence, recorded here deliberately; releasing the slot while sleeping is a separate design and is **not** in scope.

**Don't "fix" `cpu: 16` vs the 28 in `local_setup_avatar.md`.** Out of scope; noted in the spec.
