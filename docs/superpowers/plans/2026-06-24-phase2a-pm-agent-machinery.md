# Co-Science Platform — Phase 2a (PM Agent Machinery) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, fully-tested machinery of the PM agent (Increments 1–4 of the PM design): a first-class `program` substrate entity, a `Reasoner` seam with a `FakeReasoner`, a kill-safe `pm_beat` heartbeat that turns one reasoner output into proposed sprints + a report, and a runner that beats every active program. The real LLM-backed reasoner and the `coscience pm` CLI + acceptance runbook are deliberately deferred to **Phase 2b** — everything here runs against `FakeReasoner` and the unit suite stays hermetic.

**Architecture:** The LLM produces structured data; deterministic Python does all writes. A `pm_beat(substrate, program_id, reasoner)` gathers a `PMContext`, calls `reasoner.run()` **once**, atomically stages the result (with its cycle number), then idempotently submits each proposal as a `status="proposed"` sprint linked to the program, writes `report.md`, and bumps `pm.md`. The staged-commit fence makes the single non-deterministic call replay-safe across a kill at any point. The PM never approves and is never resource-leased.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest. No new dependencies. (FastAPI/MCP already present from Phase 1b; Task 3 reuses them.)

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** none added.
- **Propose-only:** `pm_beat` writes sprints **only** with `status="proposed"`. It must never set `approved`/`executing` or call any approve path. Humans approve.
- **LLM returns data; machinery writes:** the `Reasoner` returns a `PMCycleOutput`; the PM code performs every substrate write. No reasoner touches the substrate. **No real/LLM reasoner in this plan** — `FakeReasoner` only; the suite makes no network/Claude calls.
- **Kill-safe & idempotent:** the single `reasoner.run()` call per cycle is fenced behind an atomic staging write that records the cycle number. Submission is idempotent via deterministic ids `"<program_id>-c<cycle>-<suffix>"` + a skip-if-exists check. Re-running a beat must never duplicate a sprint.
- **Not resource-leased:** the PM never acquires a lease and never touches the ledger.
- **Backward compatibility:** all existing tests stay green; no behavior change to worker/dispatcher/scheduler/existing service+transport methods.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only Phase 2a is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 / 1 / 1b-* | substrate; scheduling; job control; service core + MCP + HTTP + container; PID-reuse guard | DONE |
| **2a — PM machinery** (this plan) | program entity; reasoner seam + FakeReasoner; kill-safe pm_beat; runner | **planned here** |
| 2b — PM real brain | `ClaudeCodeReasoner`, `coscience pm` CLI, manual acceptance runbook | after 2a |
| (later) dashboard | internal oversight UI over programs/sprints/results/ledger | own cycle |

Design spec: `docs/superpowers/specs/2026-06-24-pm-agent-design.md`.

---

## File Structure

```
src/coscience/
  models.py        # MODIFY (T1): ProgramStatus, Program, PMState
  substrate.py     # MODIFY (T1): program storage (load/iter/save_program, save/load_report, load/save_pm_state)
  service.py       # MODIFY (T2): list_programs, get_program (read-only)
  http_api.py      # MODIFY (T3): GET /programs, GET /programs/{id}
  mcp_server.py    # MODIFY (T3): list_programs, get_program tools
  pm_reasoner.py   # NEW (T4): PMContext, ProposedSprint, PMCycleOutput, Reasoner protocol, FakeReasoner
  pm_agent.py      # NEW (T5): gather_context; (T6): StagedCycle + staging helpers + proposal_id; (T7): pm_beat
  pm_runner.py     # NEW (T8): pm_run_once, pm_loop
  cli.py           # MODIFY (T8): `coscience program create` subcommand
tests/
  test_program_substrate.py   # NEW (T1)
  test_service_programs.py     # NEW (T2)
  test_transport_programs.py   # NEW (T3)
  test_pm_reasoner.py          # NEW (T4)
  test_pm_context.py           # NEW (T5)
  test_pm_staging.py           # NEW (T6)
  test_pm_beat.py              # NEW (T7)
  test_pm_runner.py            # NEW (T8)
  test_cli_program.py          # NEW (T8)
```

---

## Task 1: Program substrate (models + storage)

**Files:**
- Modify: `src/coscience/models.py`, `src/coscience/substrate.py`
- Test: `tests/test_program_substrate.py`

**Interfaces:**
- Produces:
  - `ProgramStatus(StrEnum)`: `ACTIVE="active"`, `PAUSED="paused"`, `CLOSED="closed"`.
  - `Program(id: str, title: str, goals: str, status: ProgramStatus = ProgramStatus.ACTIVE)`.
  - `PMState(program_id: str, cycle: int = 0, last_run: float | None = None, proposed_ids: list[str] = [], log: list[str] = [])`.
  - `Substrate`: `program_dir(id) -> Path`; `save_program(Program)`; `load_program(id) -> Program`; `iter_programs(status=None) -> list[Program]`; `save_report(id, report: str)`; `load_report(id) -> str` (`""` if absent); `load_pm_state(id) -> PMState` (default if absent); `save_pm_state(PMState)`.
- Storage layout: `programs/<id>/program.md` (frontmatter `type/title/status`, body = goals), `report.md` (plain markdown), `pm.md` (frontmatter pm state).

- [ ] **Step 1: Write the failing tests**

`tests/test_program_substrate.py`:
```python
from coscience.models import PMState, Program, ProgramStatus
from coscience.substrate import Substrate


def test_save_then_load_program(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="Cancer", goals="cure it"))
    loaded = sub.load_program("p1")
    assert loaded == Program(id="p1", title="Cancer", goals="cure it",
                             status=ProgramStatus.ACTIVE)


def test_iter_programs_empty_and_filtered(tmp_path):
    sub = Substrate(tmp_path)
    assert sub.iter_programs() == []
    sub.save_program(Program(id="a", title="A", goals="x"))
    sub.save_program(Program(id="b", title="B", goals="y", status=ProgramStatus.PAUSED))
    assert [p.id for p in sub.iter_programs()] == ["a", "b"]
    assert [p.id for p in sub.iter_programs(status=ProgramStatus.ACTIVE)] == ["a"]


def test_report_roundtrip_and_default(tmp_path):
    sub = Substrate(tmp_path)
    assert sub.load_report("p1") == ""
    sub.save_program(Program(id="p1", title="A", goals="x"))
    sub.save_report("p1", "# Status\nall good")
    assert "all good" in sub.load_report("p1")


def test_pm_state_default_and_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    assert sub.load_pm_state("p1") == PMState(program_id="p1")
    sub.save_program(Program(id="p1", title="A", goals="x"))
    sub.save_pm_state(PMState(program_id="p1", cycle=3, last_run=12.0,
                              proposed_ids=["p1-c0-a"], log=["cycle 0"]))
    assert sub.load_pm_state("p1") == PMState(program_id="p1", cycle=3, last_run=12.0,
                                              proposed_ids=["p1-c0-a"], log=["cycle 0"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_program_substrate.py -v`
Expected: FAIL with `ImportError: cannot import name 'Program'`.

- [ ] **Step 3: Add the models**

In `src/coscience/models.py`, add after `SprintStatus`:
```python
class ProgramStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
```
and after `ProgressState`:
```python
@dataclass
class Program:
    id: str
    title: str
    goals: str
    status: ProgramStatus = ProgramStatus.ACTIVE


@dataclass
class PMState:
    program_id: str
    cycle: int = 0
    last_run: float | None = None
    proposed_ids: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Add program storage to `Substrate`**

In `src/coscience/substrate.py`, extend the import line to:
```python
from coscience.models import Sprint, SprintStatus, Step, ProgressState, Result, Program, ProgramStatus, PMState
```
Add these methods to the `Substrate` class (after the results section):
```python
    # --- programs ---
    def program_dir(self, program_id: str) -> Path:
        return self.repo_root / "programs" / program_id

    def load_program(self, program_id: str) -> Program:
        text = (self.program_dir(program_id) / "program.md").read_text()
        fm, body = parse(text)
        return Program(
            id=program_id,
            title=str(fm.get("title", "")),
            goals=body.strip(),
            status=ProgramStatus(fm.get("status", "active")),
        )

    def save_program(self, program: Program) -> None:
        fm = {"type": "program", "title": program.title, "status": str(program.status)}
        d = self.program_dir(program.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "program.md").write_text(serialize(fm, program.goals.strip() + "\n"))

    def iter_programs(self, status: ProgramStatus | None = None) -> list[Program]:
        programs_dir = self.repo_root / "programs"
        if not programs_dir.is_dir():
            return []
        out = []
        for d in sorted(programs_dir.iterdir()):
            if (d / "program.md").is_file():
                p = self.load_program(d.name)
                if status is None or p.status == status:
                    out.append(p)
        return out

    def save_report(self, program_id: str, report: str) -> None:
        d = self.program_dir(program_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text(report.rstrip() + "\n")

    def load_report(self, program_id: str) -> str:
        path = self.program_dir(program_id) / "report.md"
        return path.read_text() if path.is_file() else ""

    def load_pm_state(self, program_id: str) -> PMState:
        path = self.program_dir(program_id) / "pm.md"
        if not path.is_file():
            return PMState(program_id=program_id)
        fm, _ = parse(path.read_text())
        return PMState(
            program_id=program_id,
            cycle=int(fm.get("cycle", 0)),
            last_run=fm.get("last_run"),
            proposed_ids=list(fm.get("proposed_ids", [])),
            log=list(fm.get("log", [])),
        )

    def save_pm_state(self, state: PMState) -> None:
        fm = {
            "type": "pm_state",
            "cycle": state.cycle,
            "last_run": state.last_run,
            "proposed_ids": state.proposed_ids,
            "log": state.log,
        }
        d = self.program_dir(state.program_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "pm.md").write_text(serialize(fm, f"# PM state {state.program_id}\n"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_program_substrate.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_program_substrate.py
git commit -m "feat: program substrate entity (Program, PMState, storage)"
```

---

## Task 2: Service read-only program methods

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_programs.py`

**Interfaces:**
- Consumes: `Substrate.iter_programs/load_program/load_report/load_pm_state`, `iter_sprints` (Task 1 + existing).
- Produces on `Service`:
  - `list_programs(status: str | None = None) -> list[dict]` — each `{"id","title","status","goals"}`.
  - `get_program(program_id: str) -> dict` — `{"id","title","status","goals","report","cycle","sprints":[{"id","status","goals"}]}` (sprints = those whose `program` == id). `NotFoundError` if absent.

- [ ] **Step 1: Write the failing tests**

`tests/test_service_programs.py`:
```python
import json

import pytest

from coscience.models import Program, ProgramStatus, Sprint, SprintStatus, Step
from coscience.service import NotFoundError, Service


def test_list_and_get_program(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="Cancer", goals="cure"))
    svc.substrate.save_sprint(Sprint(id="p1-s1", status=SprintStatus.PROPOSED,
                                     goals="assay", plan=[Step("s", "true")], program="p1"))
    assert svc.list_programs() == [{"id": "p1", "title": "Cancer",
                                    "status": "active", "goals": "cure"}]
    detail = svc.get_program("p1")
    assert detail["goals"] == "cure"
    assert detail["cycle"] == 0
    assert [s["id"] for s in detail["sprints"]] == ["p1-s1"]
    json.dumps(detail)  # JSON-serialisable


def test_list_programs_status_filter(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="a", title="A", goals="x"))
    svc.substrate.save_program(Program(id="b", title="B", goals="y",
                                       status=ProgramStatus.CLOSED))
    assert [p["id"] for p in svc.list_programs(status="active")] == ["a"]


def test_get_missing_program_raises(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_program("nope")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_programs.py -v`
Expected: FAIL with `AttributeError: 'Service' object has no attribute 'list_programs'`.

- [ ] **Step 3: Implement the methods**

In `src/coscience/service.py`, extend the models import to include `Program, ProgramStatus`:
```python
from coscience.models import Sprint, SprintStatus, Step, Program, ProgramStatus
```
Add to the `Service` class (after the sprints section, before results):
```python
    # --- programs (read-only) ---
    def list_programs(self, status: str | None = None) -> list[dict]:
        wanted = ProgramStatus(status) if status is not None else None
        return [{"id": p.id, "title": p.title, "status": p.status.value, "goals": p.goals}
                for p in self.substrate.iter_programs(status=wanted)]

    def get_program(self, program_id: str) -> dict:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        p = self.substrate.load_program(program_id)
        pm = self.substrate.load_pm_state(program_id)
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        return {
            "id": p.id, "title": p.title, "status": p.status.value, "goals": p.goals,
            "report": self.substrate.load_report(program_id),
            "cycle": pm.cycle,
            "sprints": [{"id": s.id, "status": s.status.value, "goals": s.goals}
                        for s in sprints],
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_programs.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_programs.py
git commit -m "feat: service read-only program methods (list/get)"
```

---

## Task 3: Program exposure over HTTP + MCP

**Files:**
- Modify: `src/coscience/http_api.py`, `src/coscience/mcp_server.py`
- Test: `tests/test_transport_programs.py`

**Interfaces:**
- Consumes: `Service.list_programs/get_program` (Task 2).
- Produces:
  - HTTP: `GET /programs` (optional `?status=`) → list; `GET /programs/{program_id}` → detail, `404` if absent (invalid `?status=` → 422, mirroring `/sprints`).
  - MCP tools: `list_programs(status=None)`, `get_program(id)` (missing → `ToolError`).

- [ ] **Step 1: Write the failing tests**

`tests/test_transport_programs.py`:
```python
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.mcp_server import build_server
from coscience.models import Program
from coscience.service import Service


def _seed(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="Cancer", goals="cure"))
    return svc


def test_http_list_and_get_program(tmp_path):
    client = TestClient(build_app(_seed(tmp_path)))
    assert [p["id"] for p in client.get("/programs").json()] == ["p1"]
    r = client.get("/programs/p1")
    assert r.status_code == 200
    assert r.json()["goals"] == "cure"
    assert client.get("/programs/nope").status_code == 404


def test_http_invalid_status_is_422(tmp_path):
    client = TestClient(build_app(_seed(tmp_path)))
    assert client.get("/programs", params={"status": "bogus"}).status_code == 422


def test_mcp_list_and_get_program(tmp_path):
    server = build_server(_seed(tmp_path))

    def call(name, args):
        r = asyncio.run(server.call_tool(name, args))
        return r[1]["result"] if isinstance(r, tuple) else json.loads(r[0].text)

    assert [p["id"] for p in call("list_programs", {})] == ["p1"]
    assert call("get_program", {"id": "p1"})["title"] == "Cancer"


def test_mcp_missing_program_raises(tmp_path):
    from mcp.server.fastmcp.exceptions import ToolError
    server = build_server(_seed(tmp_path))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("get_program", {"id": "nope"}))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_transport_programs.py -v`
Expected: FAIL (404/no route for `/programs`; unknown MCP tool `list_programs`).

- [ ] **Step 3: Add the HTTP routes**

In `src/coscience/http_api.py`, inside `build_app`, add alongside the sprint routes:
```python
    @app.get("/programs")
    def list_programs(status: str | None = Query(default=None)) -> list[dict]:
        try:
            return service.list_programs(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/programs/{program_id}")
    def get_program(program_id: str) -> dict:
        try:
            return service.get_program(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
```

- [ ] **Step 4: Add the MCP tools**

In `src/coscience/mcp_server.py`, inside `build_server`, add alongside the other tools:
```python
    @server.tool()
    def list_programs(status: str | None = None) -> list[dict]:
        """List research programs, optionally filtered by status."""
        return service.list_programs(status)

    @server.tool()
    def get_program(id: str) -> dict:
        """Get one program's detail, including its report, cycle, and sprints."""
        try:
            return service.get_program(id)
        except NotFoundError:
            raise ToolError(f"program not found: {id}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_transport_programs.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/http_api.py src/coscience/mcp_server.py tests/test_transport_programs.py
git commit -m "feat: expose programs over HTTP + MCP (read-only)"
```

---

## Task 4: Reasoner seam (`pm_reasoner.py`)

**Files:**
- Create: `src/coscience/pm_reasoner.py`
- Test: `tests/test_pm_reasoner.py`

**Interfaces:**
- Produces:
  - `@dataclass PMContext(program_id, goals, cycle, open_sprints=[], completed=[], prior_proposals=[])`.
  - `@dataclass ProposedSprint(suffix, goals, plan, priority=0, resources_required=None, rationale="")`.
  - `@dataclass PMCycleOutput(proposals=[], report="")`.
  - `class Reasoner(Protocol): def run(self, context: PMContext) -> PMCycleOutput: ...`.
  - `class FakeReasoner` — constructed with a list of `PMCycleOutput`; `run` pops the next (records each call's context in `.calls`); returns an empty `PMCycleOutput()` when exhausted.

- [ ] **Step 1: Write the failing tests**

`tests/test_pm_reasoner.py`:
```python
from coscience.pm_reasoner import (FakeReasoner, PMContext, PMCycleOutput,
                                    ProposedSprint)


def _ctx():
    return PMContext(program_id="p1", goals="cure", cycle=0)


def test_fake_reasoner_returns_scripted_outputs_in_order():
    o1 = PMCycleOutput(proposals=[ProposedSprint(suffix="a", goals="g",
                                                 plan=[{"id": "s", "run": "true"}])],
                       report="r1")
    o2 = PMCycleOutput(report="r2")
    fake = FakeReasoner([o1, o2])
    assert fake.run(_ctx()) is o1
    assert fake.run(_ctx()) is o2


def test_fake_reasoner_records_calls():
    fake = FakeReasoner([PMCycleOutput()])
    ctx = _ctx()
    fake.run(ctx)
    assert fake.calls == [ctx]


def test_fake_reasoner_empty_when_exhausted():
    fake = FakeReasoner([])
    assert fake.run(_ctx()) == PMCycleOutput()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_reasoner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.pm_reasoner'`.

- [ ] **Step 3: Implement `src/coscience/pm_reasoner.py`**

```python
"""The PM reasoner seam: the LLM (or a fake) returns structured data; the PM
machinery performs every substrate write. Keeping writes out of the reasoner is
what makes propose-only and idempotency enforceable in tested Python."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class PMContext:
    program_id: str
    goals: str
    cycle: int
    open_sprints: list[dict] = field(default_factory=list)
    completed: list[dict] = field(default_factory=list)
    prior_proposals: list[str] = field(default_factory=list)


@dataclass
class ProposedSprint:
    suffix: str
    goals: str
    plan: list[dict]
    priority: int = 0
    resources_required: dict | None = None
    rationale: str = ""


@dataclass
class PMCycleOutput:
    proposals: list[ProposedSprint] = field(default_factory=list)
    report: str = ""


class Reasoner(Protocol):
    def run(self, context: PMContext) -> PMCycleOutput:
        ...


class FakeReasoner:
    """Deterministic reasoner for tests: returns the given outputs in order,
    then empty outputs. Records each call's context in `.calls`."""

    def __init__(self, outputs: list[PMCycleOutput]):
        self._outputs = list(outputs)
        self.calls: list[PMContext] = []

    def run(self, context: PMContext) -> PMCycleOutput:
        self.calls.append(context)
        if not self._outputs:
            return PMCycleOutput()
        return self._outputs.pop(0)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_reasoner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_reasoner.py tests/test_pm_reasoner.py
git commit -m "feat: PM reasoner seam (PMContext/ProposedSprint/PMCycleOutput, FakeReasoner)"
```

---

## Task 5: `gather_context` (`pm_agent.py`)

**Files:**
- Create: `src/coscience/pm_agent.py`
- Test: `tests/test_pm_context.py`

**Interfaces:**
- Consumes: `Substrate` program/sprint/result reads; `PMContext` (Task 4).
- Produces: `gather_context(substrate, program_id: str) -> PMContext` — `goals` from the program; `cycle` from PM state; `open_sprints` = sprints with this program in status proposed/approved/executing (`{"id","status","goals"}`); `completed` = done sprints (`{"id","goals","result"}` where `result` is the first result's summary or `""`); `prior_proposals` = `pm_state.proposed_ids`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pm_context.py`:
```python
from coscience.models import (PMState, Program, Result, Sprint, SprintStatus, Step)
from coscience.pm_agent import gather_context


def test_gather_context_splits_open_and_completed(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure cancer"))
    substrate.save_pm_state(PMState(program_id="p1", cycle=2, proposed_ids=["p1-c0-a"]))
    substrate.save_sprint(Sprint(id="p1-open", status=SprintStatus.APPROVED,
                                 goals="assay", plan=[Step("s", "true")], program="p1"))
    substrate.save_sprint(Sprint(id="p1-done", status=SprintStatus.DONE, goals="prior",
                                 plan=[Step("s", "true")], program="p1",
                                 results=["p1-done-result"]))
    substrate.save_result(Result(id="p1-done-result", sprint="p1-done", summary="found X"))
    substrate.save_sprint(Sprint(id="other", status=SprintStatus.PROPOSED, goals="elsewhere",
                                 plan=[Step("s", "true")], program="p2"))

    ctx = gather_context(substrate, "p1")
    assert ctx.goals == "cure cancer"
    assert ctx.cycle == 2
    assert ctx.prior_proposals == ["p1-c0-a"]
    assert [s["id"] for s in ctx.open_sprints] == ["p1-open"]
    assert ctx.completed == [{"id": "p1-done", "goals": "prior", "result": "found X"}]


def test_gather_context_done_without_result(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    substrate.save_sprint(Sprint(id="p1-d", status=SprintStatus.DONE, goals="d",
                                 plan=[Step("s", "true")], program="p1"))
    ctx = gather_context(substrate, "p1")
    assert ctx.completed == [{"id": "p1-d", "goals": "d", "result": ""}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.pm_agent'`.

- [ ] **Step 3: Implement `gather_context` in `src/coscience/pm_agent.py`**

```python
"""The PM heartbeat: gather context, call the reasoner once (fenced behind an
atomic staging commit), then idempotently submit proposed sprints + write the
report. Deterministic and kill-safe; the reasoner does no writes."""
from __future__ import annotations

from coscience.models import SprintStatus
from coscience.pm_reasoner import PMContext


def gather_context(substrate, program_id: str) -> PMContext:
    program = substrate.load_program(program_id)
    pm = substrate.load_pm_state(program_id)
    open_sprints: list[dict] = []
    completed: list[dict] = []
    for s in substrate.iter_sprints():
        if s.program != program_id:
            continue
        if s.status == SprintStatus.DONE:
            result = ""
            if s.results:
                try:
                    result = substrate.load_result(s.results[0]).summary
                except OSError:
                    result = ""
            completed.append({"id": s.id, "goals": s.goals, "result": result})
        elif s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED,
                          SprintStatus.EXECUTING):
            open_sprints.append({"id": s.id, "status": s.status.value, "goals": s.goals})
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        open_sprints=open_sprints, completed=completed,
        prior_proposals=list(pm.proposed_ids),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_context.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_pm_context.py
git commit -m "feat: PM gather_context (program state -> PMContext)"
```

---

## Task 6: Staging helpers + deterministic ids (`pm_agent.py`)

**Files:**
- Modify: `src/coscience/pm_agent.py`
- Test: `tests/test_pm_staging.py`

**Interfaces:**
- Produces (in `pm_agent.py`):
  - `@dataclass StagedCycle(cycle: int, output: PMCycleOutput)`.
  - `proposal_id(program_id: str, cycle: int, suffix: str) -> str` → `f"{program_id}-c{cycle}-{suffix}"`.
  - `write_staging(substrate, program_id: str, cycle: int, output: PMCycleOutput) -> None` — atomic (write `.json.tmp` then `os.replace`) to `programs/<id>/.pm/cycle-staging.json`; records the cycle.
  - `read_staging(substrate, program_id: str) -> StagedCycle | None` — `None` if absent.
  - `clear_staging(substrate, program_id: str) -> None` — remove the file (no-op if absent).

- [ ] **Step 1: Write the failing tests**

`tests/test_pm_staging.py`:
```python
from coscience.pm_agent import (StagedCycle, clear_staging, proposal_id,
                                read_staging, write_staging)
from coscience.pm_reasoner import PMCycleOutput, ProposedSprint


def test_proposal_id_format():
    assert proposal_id("p1", 3, "assay") == "p1-c3-assay"


def test_staging_roundtrip_carries_cycle(substrate):
    out = PMCycleOutput(
        proposals=[ProposedSprint(suffix="a", goals="g", plan=[{"id": "s", "run": "true"}],
                                  priority=2, resources_required={"gpu": 1.0})],
        report="the report")
    assert read_staging(substrate, "p1") is None
    write_staging(substrate, "p1", 5, out)
    staged = read_staging(substrate, "p1")
    assert staged == StagedCycle(cycle=5, output=out)


def test_clear_staging(substrate):
    write_staging(substrate, "p1", 0, PMCycleOutput(report="r"))
    clear_staging(substrate, "p1")
    assert read_staging(substrate, "p1") is None
    clear_staging(substrate, "p1")  # no-op, no error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_staging.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_staging'`.

- [ ] **Step 3: Add staging helpers to `pm_agent.py`**

Extend the imports at the top of `src/coscience/pm_agent.py`:
```python
import json
import os
from dataclasses import dataclass

from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint
```
Append:
```python
@dataclass
class StagedCycle:
    cycle: int
    output: PMCycleOutput


def proposal_id(program_id: str, cycle: int, suffix: str) -> str:
    return f"{program_id}-c{cycle}-{suffix}"


def _staging_path(substrate, program_id: str):
    return substrate.program_dir(program_id) / ".pm" / "cycle-staging.json"


def write_staging(substrate, program_id: str, cycle: int, output: PMCycleOutput) -> None:
    path = _staging_path(substrate, program_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cycle": cycle,
        "report": output.report,
        "proposals": [
            {"suffix": p.suffix, "goals": p.goals, "plan": p.plan,
             "priority": p.priority, "resources_required": p.resources_required,
             "rationale": p.rationale}
            for p in output.proposals
        ],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)  # atomic on POSIX


def read_staging(substrate, program_id: str) -> "StagedCycle | None":
    path = _staging_path(substrate, program_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    output = PMCycleOutput(
        report=data.get("report", ""),
        proposals=[ProposedSprint(**p) for p in data.get("proposals", [])],
    )
    return StagedCycle(cycle=int(data["cycle"]), output=output)


def clear_staging(substrate, program_id: str) -> None:
    path = _staging_path(substrate, program_id)
    if path.is_file():
        path.unlink()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_staging.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_pm_staging.py
git commit -m "feat: PM staging helpers + deterministic proposal ids"
```

---

## Task 7: `pm_beat` — the kill-safe heartbeat (`pm_agent.py`)

**Files:**
- Modify: `src/coscience/pm_agent.py`
- Test: `tests/test_pm_beat.py`

**Interfaces:**
- Consumes: `gather_context`, staging helpers, `proposal_id` (Tasks 5–6); `Reasoner` (Task 4); `Substrate` writes.
- Produces: `pm_beat(substrate, program_id: str, reasoner, now: float | None = None) -> dict`.
  - Reads PM state; if no staged cycle, gathers context, calls `reasoner.run()` **once**, and atomically stages the output **with the current cycle**. The staged cycle (not the live PM cycle) drives id generation, so resume after a cycle bump is safe.
  - For each staged proposal, computes `proposal_id(program_id, staged_cycle, suffix)`; if a sprint with that id already exists, **skips**; else saves a `Sprint(status=PROPOSED, program=program_id, ...)`.
  - Writes `report.md`; updates PM state (`cycle = staged_cycle + 1`, `last_run`, appends new proposed ids, appends a log line); clears staging.
  - Returns `{"program","cycle","submitted":[ids],"proposed":[ids]}` (`cycle` = the staged cycle just run).

**Critical ordering (kill-safety):** stage (commit) → submit → report → bump PM state → clear staging. Every step after the commit is idempotent and uses the **staged** cycle, so a kill anywhere replays to the same result.

- [ ] **Step 1: Write the failing tests**

`tests/test_pm_beat.py`:
```python
import pytest

from coscience.models import Program, SprintStatus
from coscience.pm_agent import pm_beat, read_staging, write_staging
from coscience.pm_reasoner import PMCycleOutput, ProposedSprint


class BoomReasoner:
    """Fails if run() is called — proves resume does not re-reason."""
    def run(self, context):
        raise AssertionError("reasoner must not be called on resume")


def _out(suffix="a", report="r"):
    return PMCycleOutput(
        proposals=[ProposedSprint(suffix=suffix, goals="do " + suffix,
                                  plan=[{"id": "s", "run": "true"}], priority=1)],
        report=report)


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_fresh_beat_proposes_and_reports(substrate):
    _prog(substrate)
    from coscience.pm_reasoner import FakeReasoner
    summary = pm_beat(substrate, "p1", FakeReasoner([_out("a", "report-0")]))

    assert summary["submitted"] == ["p1-c0-a"]
    sprint = substrate.load_sprint("p1-c0-a")
    assert sprint.status == SprintStatus.PROPOSED      # propose-only
    assert sprint.program == "p1"
    assert sprint.priority == 1
    assert "report-0" in substrate.load_report("p1")
    pm = substrate.load_pm_state("p1")
    assert pm.cycle == 1                                # bumped
    assert pm.proposed_ids == ["p1-c0-a"]
    assert read_staging(substrate, "p1") is None        # cleared


def test_second_beat_uses_next_cycle(substrate):
    _prog(substrate)
    from coscience.pm_reasoner import FakeReasoner
    fake = FakeReasoner([_out("a"), _out("b")])
    pm_beat(substrate, "p1", fake)
    summary = pm_beat(substrate, "p1", fake)
    assert summary["submitted"] == ["p1-c1-b"]
    assert substrate.load_pm_state("p1").cycle == 2
    assert substrate.load_pm_state("p1").proposed_ids == ["p1-c0-a", "p1-c1-b"]


def test_rerun_same_cycle_is_idempotent(substrate):
    # Stage a cycle, then run twice from the same staged state (simulating a
    # crash before clear). The reasoner must NOT be called, and no duplicate.
    _prog(substrate)
    write_staging(substrate, "p1", 0, _out("a", "staged-report"))
    s1 = pm_beat(substrate, "p1", BoomReasoner())   # resumes from staging
    assert s1["submitted"] == ["p1-c0-a"]
    # Re-stage the same cycle 0 (as if the bump didn't persist) and re-run:
    write_staging(substrate, "p1", 0, _out("a", "staged-report"))
    s2 = pm_beat(substrate, "p1", BoomReasoner())
    assert s2["submitted"] == []                     # already exists -> skipped
    assert len([s for s in substrate.iter_sprints() if s.id == "p1-c0-a"]) == 1


def test_resume_after_cycle_bump_does_not_shift_ids(substrate):
    # Simulate: staged cycle 0 fully applied + pm.cycle already bumped to 1,
    # but staging not yet cleared. Resume must replay cycle-0 ids, not cycle-1.
    _prog(substrate)
    from coscience.models import PMState
    write_staging(substrate, "p1", 0, _out("a"))
    substrate.save_pm_state(PMState(program_id="p1", cycle=1,
                                    proposed_ids=["p1-c0-a"]))
    summary = pm_beat(substrate, "p1", BoomReasoner())
    assert summary["cycle"] == 0
    assert summary["submitted"] == []                # p1-c0-a already proposed
    assert substrate.load_sprint("p1-c0-a").status == SprintStatus.PROPOSED
    assert read_staging(substrate, "p1") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_beat.py -v`
Expected: FAIL with `ImportError: cannot import name 'pm_beat'`.

- [ ] **Step 3: Implement `pm_beat`**

Extend the imports at the top of `src/coscience/pm_agent.py`:
```python
import time

from coscience.models import Sprint, SprintStatus, Step
```
Append:
```python
def pm_beat(substrate, program_id: str, reasoner, now: float | None = None) -> dict:
    """Run one bounded, kill-safe PM cycle for a program. Returns a summary."""
    pm = substrate.load_pm_state(program_id)

    staged = read_staging(substrate, program_id)
    if staged is None:
        cycle = pm.cycle
        context = gather_context(substrate, program_id)
        output = reasoner.run(context)                 # the ONE reasoner call
        write_staging(substrate, program_id, cycle, output)   # COMMIT POINT
        staged = StagedCycle(cycle=cycle, output=output)

    cycle = staged.cycle
    submitted: list[str] = []
    proposed: list[str] = []
    for prop in staged.output.proposals:
        sid = proposal_id(program_id, cycle, prop.suffix)
        proposed.append(sid)
        if (substrate.sprint_dir(sid) / "sprint.md").is_file():
            continue                                   # idempotent skip
        substrate.save_sprint(Sprint(
            id=sid, status=SprintStatus.PROPOSED, goals=prop.goals,
            plan=[Step.from_dict(s) for s in prop.plan],
            program=program_id, priority=prop.priority,
            resources_required={k: float(v)
                                for k, v in (prop.resources_required or {}).items()},
        ))
        submitted.append(sid)

    substrate.save_report(program_id, staged.output.report)

    pm.cycle = cycle + 1
    pm.last_run = time.time() if now is None else now
    for sid in proposed:
        if sid not in pm.proposed_ids:
            pm.proposed_ids.append(sid)
    pm.log.append(f"cycle {cycle}: proposed {proposed}")
    substrate.save_pm_state(pm)

    clear_staging(substrate, program_id)
    return {"program": program_id, "cycle": cycle,
            "submitted": submitted, "proposed": proposed}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_beat.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass. Record the count.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_pm_beat.py
git commit -m "feat: kill-safe pm_beat heartbeat (stage->submit->report->bump->clear)"
```

---

## Task 8: Runner + `coscience program create` CLI

**Files:**
- Create: `src/coscience/pm_runner.py`
- Modify: `src/coscience/cli.py`
- Test: `tests/test_pm_runner.py`, `tests/test_cli_program.py`

**Interfaces:**
- Consumes: `pm_beat` (Task 7); `Substrate.iter_programs`; `ProgramStatus`.
- Produces:
  - `pm_run_once(substrate, reasoner) -> list[dict]` — calls `pm_beat` for every **active** program (sorted by id); returns the list of beat summaries.
  - `pm_loop(substrate, reasoner, interval=5.0, max_rounds=None, sleep=time.sleep) -> int` — repeats `pm_run_once` `max_rounds` times (or forever if `None`), sleeping `interval` between rounds (injectable `sleep` for tests); returns rounds run.
  - CLI: `coscience program create --repo R --id ID --title T --goals G` writes a program via `Substrate.save_program` and prints the id. (The `coscience pm` run command is intentionally deferred to Phase 2b, where the real reasoner exists.)

- [ ] **Step 1: Write the failing tests**

`tests/test_pm_runner.py`:
```python
from coscience.models import Program, ProgramStatus
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.pm_runner import pm_loop, pm_run_once


def _out(suffix):
    return PMCycleOutput(proposals=[ProposedSprint(suffix=suffix, goals="g",
                                                   plan=[{"id": "s", "run": "true"}])],
                         report="r")


def test_run_once_beats_only_active_programs(substrate):
    substrate.save_program(Program(id="a", title="A", goals="x"))
    substrate.save_program(Program(id="b", title="B", goals="y",
                                   status=ProgramStatus.PAUSED))
    fake = FakeReasoner([_out("z"), _out("z")])
    summaries = pm_run_once(substrate, fake)
    assert [s["program"] for s in summaries] == ["a"]      # only the active one
    assert substrate.load_sprint("a-c0-z").program == "a"


def test_pm_loop_runs_max_rounds_with_injected_sleep(substrate):
    substrate.save_program(Program(id="a", title="A", goals="x"))
    fake = FakeReasoner([_out("p"), _out("q")])
    sleeps = []
    rounds = pm_loop(substrate, fake, interval=9.0, max_rounds=2,
                     sleep=lambda s: sleeps.append(s))
    assert rounds == 2
    assert substrate.load_pm_state("a").cycle == 2        # two beats happened
    assert sleeps == [9.0]                                 # slept between, not after last
```

`tests/test_cli_program.py`:
```python
from coscience.cli import main
from coscience.substrate import Substrate


def test_program_create_writes_program(tmp_path, capsys):
    rc = main(["program", "create", "--repo", str(tmp_path),
               "--id", "p1", "--title", "Cancer", "--goals", "cure it"])
    assert rc == 0
    assert "p1" in capsys.readouterr().out
    p = Substrate(tmp_path).load_program("p1")
    assert p.title == "Cancer" and p.goals == "cure it"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_runner.py tests/test_cli_program.py -v`
Expected: FAIL (`ModuleNotFoundError: coscience.pm_runner`; CLI has no `program` subcommand).

- [ ] **Step 3: Implement `src/coscience/pm_runner.py`**

```python
"""Runner over the PM heartbeat: beat every active program. Reasoner is injected
(FakeReasoner in tests; the real ClaudeCodeReasoner is wired in Phase 2b)."""
from __future__ import annotations

import time

from coscience.models import ProgramStatus
from coscience.pm_agent import pm_beat


def pm_run_once(substrate, reasoner) -> list[dict]:
    summaries = []
    for program in substrate.iter_programs(status=ProgramStatus.ACTIVE):
        summaries.append(pm_beat(substrate, program.id, reasoner))
    return summaries


def pm_loop(substrate, reasoner, interval: float = 5.0, max_rounds: int | None = None,
            sleep=time.sleep) -> int:
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        pm_run_once(substrate, reasoner)
        rounds += 1
        if max_rounds is None or rounds < max_rounds:
            sleep(interval)
    return rounds
```

- [ ] **Step 4: Add the `program create` CLI subcommand**

In `src/coscience/cli.py`, add `Program` to the models import:
```python
from coscience.models import BeatOutcome, Program
```
Register the subcommand in `main` (after the `dispatch` parser, before `args = parser.parse_args(argv)`):
```python
    pg = sub.add_parser("program", help="manage research programs")
    pgsub = pg.add_subparsers(dest="program_command", required=True)
    pgc = pgsub.add_parser("create", help="create a program")
    pgc.add_argument("--repo", required=True, type=Path)
    pgc.add_argument("--id", required=True)
    pgc.add_argument("--title", required=True)
    pgc.add_argument("--goals", required=True)
```
Handle it (before the final `parser.error(...)`):
```python
    if args.command == "program":
        if args.program_command == "create":
            Substrate(args.repo).save_program(
                Program(id=args.id, title=args.title, goals=args.goals))
            print(args.id)
            return 0
```

- [ ] **Step 5: Run the tests to verify they pass, then the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_runner.py tests/test_cli_program.py -v`
Expected: 3 passed.

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass. Record the count.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_runner.py src/coscience/cli.py tests/test_pm_runner.py tests/test_cli_program.py
git commit -m "feat: PM runner (pm_run_once/pm_loop) + program create CLI"
```

---

## Self-Review

**Spec coverage (against `2026-06-24-pm-agent-design.md`):**
- §4 program entity (Program/PMState + storage) → Task 1 ✓
- §4 read-only program exposure through Service → Task 2; via MCP/HTTP → Task 3 ✓
- §3 reasoner seam (PMContext/ProposedSprint/PMCycleOutput/Reasoner/FakeReasoner) → Task 4 ✓
- §3/§5 PMContext gathered from substrate → Task 5 ✓
- §5 staged-commit kill-safety + deterministic ids → Tasks 6–7 ✓
- §5 propose-only submission, report + pm.md persistence, idempotency, resume-after-cycle-bump → Task 7 ✓
- §6 runner over active programs (reasoner-injected) + minimal program-create surface → Task 8 ✓
- §6 `coscience pm` run command, §3 `ClaudeCodeReasoner`, §7 acceptance runbook → **deferred to Phase 2b** (explicit; this plan stays hermetic) ✓
- §2 propose-only / human-gated approval → Global Constraints + Task 7 (status=PROPOSED only) ✓
- §3 PM not resource-leased → no ledger touch anywhere in this plan ✓

**Placeholder scan:** complete code in every step; real assertions; no TBD. ✓

**Type consistency:** `Program/PMState/ProgramStatus`; `Substrate.load/iter/save_program`, `save/load_report`, `load/save_pm_state`; `Service.list_programs/get_program`; `PMContext/ProposedSprint/PMCycleOutput/Reasoner/FakeReasoner`; `gather_context(substrate, program_id)`; `StagedCycle`, `proposal_id`, `write/read/clear_staging`; `pm_beat(substrate, program_id, reasoner, now=None)`; `pm_run_once/pm_loop`. Names used in later tasks match earlier definitions. ✓

**Known simplifications (intentional):**
- `FakeReasoner` only; the real LLM (`ClaudeCodeReasoner`) and `coscience pm` CLI arrive in Phase 2b behind the same seam.
- Program creation is a thin CLI/`save_program` path (not an open submission endpoint).
- No per-cycle proposal cap (propose-only already gates compute; a cap can be added to `pm_beat` later if wanted).
- The PM reads current on-disk state each beat; concurrent dispatcher writes are eventually-consistent (the PM only submits proposals; humans gate approval).
```
