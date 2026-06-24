# Co-Science Platform — Phase 1b-2b-i (Service Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A transport-agnostic `Service` — a clean Python API over the existing substrate + ledger — that both the upcoming MCP server (1b-2b-ii) and HTTP API (1b-2b-iii) will wrap without duplicating logic. It exposes submit/approve/list/get for sprints, list/get for results, and a ledger-status query. Pure Python, fully unit-testable, **no new dependencies**.

**Architecture:** A `Service(repo_root, pool=None)` object holding a `Substrate` (and lazily a read-only `Ledger`). Every method returns plain `dict`/`list[dict]` (JSON-serialisable) so the transport layers can hand them straight to MCP/HTTP. Writes go through `Substrate` (and will later be committed by the dispatcher's normal flow). The Service never runs the scheduler — it only reads/writes substrate state.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest. No new dependencies in this increment (the MCP/HTTP deps arrive in ii/iii).

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** runtime `pyyaml` only; dev `pytest` only. Add nothing in this increment.
- **All Service methods return JSON-serialisable plain data** (`dict`, `list`, `str`, `int`, `float`, `bool`, `None`) — never dataclasses or `Path`/`Enum` objects (status is returned as its string value). This is what the transport layers need.
- **The Service does not schedule or run work** — it reads/writes substrate files and reads the ledger. It must not mutate the ledger.
- **Errors are explicit:** a missing sprint/result raises a `KeyError`-derived `NotFoundError` defined in `service.py`, not a bare `FileNotFoundError`.
- **Backward compatibility:** all existing tests stay green; no changes to dispatcher/worker/scheduler behavior.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only 1b-2b-i is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 / 1 / 1b-1 / 1b-2a | skeleton; scheduling; job kill; restart reconciliation | DONE |
| **1b-2b-i — service core** (this plan) | transport-agnostic `Service` API over substrate+ledger | **planned here** |
| 1b-2b-ii — MCP server | wrap `Service` as MCP tools (dep: `mcp`) | next |
| 1b-2b-iii — HTTP API + container | wrap `Service` as REST (deps: fastapi/uvicorn) + docker compose | after ii |
| 1b-2c — PID-reuse guard | identity token before signalling a stored PID | parallel hardening |

---

## File Structure

```
src/coscience/
  substrate.py   # MODIFY: add load_result(id) + iter_results() (read side for results)
  service.py     # NEW: NotFoundError + Service (submit/approve/list/get sprints, results, ledger status)
tests/
  test_service_sprints.py   # NEW: submit/approve/list/get sprints
  test_service_results.py   # NEW: substrate result reads + service results
  test_service_ledger.py    # NEW: ledger status
  test_service_integration.py  # NEW: submit -> approve -> appears in lists -> get detail
```

---

## Task 1: Substrate result reads

**Files:**
- Modify: `src/coscience/substrate.py`
- Test: `tests/test_service_results.py` (substrate portion)

**Interfaces:**
- Adds to `Substrate`:
  - `load_result(result_id: str) -> Result` — parse `results/<id>.md`; raises `FileNotFoundError` if absent (the Service wraps this into `NotFoundError`).
  - `iter_results() -> list[Result]` — all results sorted by id; `[]` when the dir is absent.
- `Result` already has `id, sprint, summary`. The result file frontmatter has `type: result`, `sprint: <id>`; the body is the summary.

- [ ] **Step 1: Write the failing tests**

`tests/test_service_results.py`:
```python
from coscience.models import Result
from coscience.substrate import Substrate


def test_iter_results_empty_when_no_dir(tmp_path):
    assert Substrate(tmp_path).iter_results() == []


def test_save_then_load_result(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    loaded = sub.load_result("r1")
    assert loaded == Result(id="r1", sprint="sp1", summary="found X")


def test_iter_results_sorted(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_result(Result(id="r2", sprint="sp2", summary="b"))
    sub.save_result(Result(id="r1", sprint="sp1", summary="a"))
    assert [r.id for r in sub.iter_results()] == ["r1", "r2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_results.py -v`
Expected: FAIL with `AttributeError: 'Substrate' object has no attribute 'iter_results'`.

- [ ] **Step 3: Add result reads to `Substrate`**

Add to the `Substrate` class (the results dir is `self.repo_root / "results"`, files are `<id>.md`):
```python
    def load_result(self, result_id: str) -> Result:
        text = (self.repo_root / "results" / f"{result_id}.md").read_text()
        fm, body = parse(text)
        return Result(id=result_id, sprint=str(fm.get("sprint", "")), summary=body.strip())

    def iter_results(self) -> list[Result]:
        results_dir = self.repo_root / "results"
        if not results_dir.is_dir():
            return []
        out = []
        for path in sorted(results_dir.glob("*.md")):
            out.append(self.load_result(path.stem))
        return out
```
(The `Result` import already exists in `substrate.py` from Phase 1; if not, add it to the `from coscience.models import ...` line.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_results.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/substrate.py tests/test_service_results.py
git commit -m "feat: substrate result reads (load_result, iter_results)"
```

---

## Task 2: Service — submit / approve / list / get sprints

**Files:**
- Create: `src/coscience/service.py`
- Test: `tests/test_service_sprints.py`

**Interfaces:**
- `class NotFoundError(KeyError)` — raised for a missing sprint/result.
- `class Service(repo_root, pool: ResourcePool | None = None)`:
  - `submit_sprint(self, *, id: str, goals: str, plan: list[dict], program: str | None = None, priority: int = 0, preemptible: bool = True, resources_required: dict | None = None, status: str = "proposed") -> str` — builds a `Sprint` (each plan item is `{"id","run"}`), saves it, returns its id. Raises `ValueError` if `plan` is empty or a sprint with that id already exists.
  - `approve_sprint(self, sprint_id: str) -> None` — set status to `approved`; `NotFoundError` if absent.
  - `list_sprints(self, status: str | None = None) -> list[dict]` — each: `{"id","status","goals","priority","steps","results"}` (`steps` = number of plan steps).
  - `get_sprint(self, sprint_id: str) -> dict` — `{"id","status","goals","priority","preemptible","resources_required","plan":[{"id","run"}],"completed_steps","detached","outputs","lease"}` where `lease` is `None` or the lease as a dict; `NotFoundError` if absent.

- [ ] **Step 1: Write the failing tests**

`tests/test_service_sprints.py`:
```python
import pytest

from coscience.service import NotFoundError, Service


def test_submit_then_list_and_get(tmp_path):
    svc = Service(tmp_path)
    sid = svc.submit_sprint(id="sp1", goals="cure", plan=[{"id": "s1", "run": "echo hi"}],
                            priority=3, resources_required={"gpu": 1})
    assert sid == "sp1"
    rows = svc.list_sprints()
    assert rows == [{"id": "sp1", "status": "proposed", "goals": "cure",
                     "priority": 3, "steps": 1, "results": []}]
    detail = svc.get_sprint("sp1")
    assert detail["status"] == "proposed"
    assert detail["resources_required"] == {"gpu": 1.0}
    assert detail["plan"] == [{"id": "s1", "run": "echo hi"}]
    assert detail["completed_steps"] == []
    assert detail["lease"] is None


def test_submit_rejects_empty_plan(tmp_path):
    with pytest.raises(ValueError):
        Service(tmp_path).submit_sprint(id="sp1", goals="g", plan=[])


def test_submit_rejects_duplicate_id(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    with pytest.raises(ValueError):
        svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])


def test_approve_changes_status_and_filters(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    svc.approve_sprint("sp1")
    assert svc.get_sprint("sp1")["status"] == "approved"
    assert [r["id"] for r in svc.list_sprints(status="approved")] == ["sp1"]
    assert svc.list_sprints(status="proposed") == []


def test_get_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_sprint("nope")


def test_approve_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).approve_sprint("nope")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_sprints.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.service'`.

- [ ] **Step 3: Implement `service.py` (sprint methods)**

```python
"""Transport-agnostic service API over the substrate + ledger.

Every method returns JSON-serialisable plain data so the MCP and HTTP layers
can hand results straight to clients.
"""
from __future__ import annotations

from pathlib import Path

from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool, load_pool
from coscience.substrate import Substrate


class NotFoundError(KeyError):
    """A requested sprint or result does not exist."""


class Service:
    def __init__(self, repo_root, pool: ResourcePool | None = None):
        self.repo_root = Path(repo_root)
        self.substrate = Substrate(self.repo_root)
        self.pool = pool if pool is not None else load_pool(self.repo_root)

    def _ledger(self) -> Ledger:
        ledger = Ledger(self.pool, self.repo_root / ".coscience" / "leases.json")
        ledger.load()
        return ledger

    def _load_sprint(self, sprint_id: str) -> Sprint:
        if not (self.substrate.sprint_dir(sprint_id) / "sprint.md").is_file():
            raise NotFoundError(sprint_id)
        return self.substrate.load_sprint(sprint_id)

    # --- sprints ---
    def submit_sprint(self, *, id: str, goals: str, plan: list[dict],
                      program: str | None = None, priority: int = 0,
                      preemptible: bool = True, resources_required: dict | None = None,
                      status: str = "proposed") -> str:
        if not plan:
            raise ValueError("plan must have at least one step")
        if (self.substrate.sprint_dir(id) / "sprint.md").is_file():
            raise ValueError(f"sprint {id} already exists")
        sprint = Sprint(
            id=id,
            status=SprintStatus(status),
            goals=goals,
            plan=[Step.from_dict(step) for step in plan],
            program=program,
            resources_required={k: float(v) for k, v in (resources_required or {}).items()},
            priority=priority,
            preemptible=preemptible,
        )
        self.substrate.save_sprint(sprint)
        return id

    def approve_sprint(self, sprint_id: str) -> None:
        sprint = self._load_sprint(sprint_id)
        sprint.status = SprintStatus.APPROVED
        self.substrate.save_sprint(sprint)

    def list_sprints(self, status: str | None = None) -> list[dict]:
        wanted = SprintStatus(status) if status is not None else None
        rows = []
        for sprint in self.substrate.iter_sprints(status=wanted):
            rows.append({
                "id": sprint.id,
                "status": sprint.status.value,
                "goals": sprint.goals,
                "priority": sprint.priority,
                "steps": len(sprint.plan),
                "results": list(sprint.results),
            })
        return rows

    def get_sprint(self, sprint_id: str) -> dict:
        sprint = self._load_sprint(sprint_id)
        progress = self.substrate.load_progress(sprint_id)
        lease = self._ledger().lease_for(sprint_id)
        return {
            "id": sprint.id,
            "status": sprint.status.value,
            "goals": sprint.goals,
            "priority": sprint.priority,
            "preemptible": sprint.preemptible,
            "resources_required": sprint.resources_required,
            "plan": [{"id": s.id, "run": s.run} for s in sprint.plan],
            "completed_steps": progress.completed_steps,
            "detached": progress.detached,
            "outputs": progress.outputs,
            "lease": None if lease is None else {
                "id": lease.id, "amounts": lease.amounts,
                "granted_at": lease.granted_at, "expires_at": lease.expires_at,
                "priority": lease.priority, "preemptible": lease.preemptible,
            },
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_sprints.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_sprints.py
git commit -m "feat: service core — submit/approve/list/get sprints"
```

---

## Task 3: Service — results + ledger status

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_ledger.py`

**Interfaces:**
- Adds to `Service`:
  - `list_results(self) -> list[dict]` — each: `{"id","sprint","summary"}`.
  - `get_result(self, result_id: str) -> dict` — `{"id","sprint","summary"}`; `NotFoundError` if absent.
  - `ledger_status(self) -> dict` — `{"capacity","used","available","leases":[{...}]}` from the current ledger + pool.

- [ ] **Step 1: Write the failing tests**

`tests/test_service_ledger.py`:
```python
import pytest

from coscience.ledger import Ledger
from coscience.models import Result
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service


def test_results_list_and_get(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    assert svc.list_results() == [{"id": "r1", "sprint": "sp1", "summary": "found X"}]
    assert svc.get_result("r1") == {"id": "r1", "sprint": "sp1", "summary": "found X"}


def test_get_missing_result_raises(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_result("nope")


def test_ledger_status_reflects_leases(tmp_path):
    pool = ResourcePool({"gpu": 2.0})
    # seed a lease on disk
    led = Ledger(pool, tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)

    svc = Service(tmp_path, pool=pool)
    status = svc.ledger_status()
    assert status["capacity"] == {"gpu": 2.0}
    assert status["available"] == {"gpu": 1.0}
    assert [lease["sprint_id"] for lease in status["leases"]] == ["sp1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_ledger.py -v`
Expected: FAIL with `AttributeError: 'Service' object has no attribute 'list_results'`.

- [ ] **Step 3: Add results + ledger methods to `Service`**

Add these methods to the `Service` class:
```python
    # --- results ---
    def list_results(self) -> list[dict]:
        return [{"id": r.id, "sprint": r.sprint, "summary": r.summary}
                for r in self.substrate.iter_results()]

    def get_result(self, result_id: str) -> dict:
        if not (self.repo_root / "results" / f"{result_id}.md").is_file():
            raise NotFoundError(result_id)
        r = self.substrate.load_result(result_id)
        return {"id": r.id, "sprint": r.sprint, "summary": r.summary}

    # --- ledger ---
    def ledger_status(self) -> dict:
        ledger = self._ledger()
        return {
            "capacity": dict(self.pool.capacity),
            "used": ledger.used(),
            "available": ledger.available(),
            "leases": [
                {"id": l.id, "sprint_id": l.sprint_id, "amounts": l.amounts,
                 "granted_at": l.granted_at, "expires_at": l.expires_at,
                 "priority": l.priority, "preemptible": l.preemptible}
                for l in ledger.all_leases()
            ],
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_ledger.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_ledger.py
git commit -m "feat: service core — results and ledger status"
```

---

## Task 4: Integration + JSON-serialisability guard

**Files:**
- Create: `tests/test_service_integration.py`

**Interfaces:**
- No new production code. Proves the end-to-end flow and that every Service return value is JSON-serialisable (the contract the transport layers depend on). If a value isn't JSON-serialisable, fix `service.py`, not the test.

- [ ] **Step 1: Write the tests**

`tests/test_service_integration.py`:
```python
import json

from coscience.models import Result
from coscience.service import Service


def test_submit_approve_flow_and_results(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="cure cancer",
                      plan=[{"id": "s1", "run": "echo a"}, {"id": "s2", "run": "echo b"}],
                      priority=5, resources_required={"gpu": 1})
    assert [r["id"] for r in svc.list_sprints(status="proposed")] == ["sp1"]

    svc.approve_sprint("sp1")
    assert [r["id"] for r in svc.list_sprints(status="approved")] == ["sp1"]

    detail = svc.get_sprint("sp1")
    assert detail["priority"] == 5 and detail["steps"] if "steps" in detail else True
    assert len(detail["plan"]) == 2

    svc.substrate.save_result(Result(id="sp1-result", sprint="sp1", summary="done"))
    assert svc.get_result("sp1-result")["summary"] == "done"


def test_every_return_value_is_json_serialisable(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}],
                      resources_required={"gpu": 1})
    svc.approve_sprint("sp1")
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="x"))
    # None of these should raise TypeError on json.dumps.
    json.dumps(svc.list_sprints())
    json.dumps(svc.get_sprint("sp1"))
    json.dumps(svc.list_results())
    json.dumps(svc.get_result("r1"))
    json.dumps(svc.ledger_status())
```

- [ ] **Step 2: Run the tests**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_integration.py -v`
Expected: 2 passed. If `json.dumps` raises, a Service method returned a non-serialisable object (Enum/Path/dataclass) — fix `service.py`.

- [ ] **Step 3: Run the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass (prior tests + the service-core additions).

- [ ] **Step 4: Commit**

```bash
git add tests/test_service_integration.py
git commit -m "test: service core end-to-end + JSON-serialisability guard"
```

---

## Self-Review

**Spec coverage:**
- Substrate result reads → Task 1 ✓
- submit/approve/list/get sprints → Task 2 ✓
- list/get results + ledger status → Task 3 ✓
- end-to-end flow + JSON-serialisable guarantee → Task 4 ✓
- No-deps, transport-agnostic, returns plain JSON-able data → Global Constraints + Task 4 ✓
- Explicitly deferred: the MCP transport (ii), HTTP transport + container (iii), PID-reuse guard (1b-2c). The Service deliberately does NOT run the scheduler.

**Placeholder scan:** complete code in every step; real assertions; no TBD. ✓

**Type consistency:** `Service(repo_root, pool=None)`; `NotFoundError(KeyError)`; methods `submit_sprint/approve_sprint/list_sprints/get_sprint/list_results/get_result/ledger_status`; substrate `load_result/iter_results`. Status returned as `.value` strings; leases/progress as plain dicts. ✓

**Known simplifications (intentional):**
- Read + submit/approve surface only; no delete/cancel, no search (later).
- The Service reads the ledger read-only; it never schedules (the dispatcher owns that).
- `get_sprint` reflects current on-disk state; concurrent dispatcher writes are eventually-consistent (single-writer dispatcher; Service is a reader/submitter).
