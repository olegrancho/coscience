# Co-Science Platform — Phase 1b-2b-ii (MCP Server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing transport-agnostic `coscience.service.Service` (built in 1b-2b-i) as an **MCP server** so an LLM agent (the future PM agent, or a human via an MCP client) can submit/approve/inspect sprints and read results/ledger status over the Model Context Protocol. A thin wrapper — all logic stays in `Service`; this layer only registers tools, marshals arguments, and translates `NotFoundError` into a clean tool error.

**Architecture:** A `build_server(service: Service, name="coscience") -> FastMCP` factory that registers seven tools, each a thin call into `Service`. A `main()` entry resolves a repo root (env `COSCIENCE_REPO`, else cwd), constructs a `Service`, builds the server, and runs it over stdio. Because `build_server` takes a `Service`, the whole tool surface is unit-testable in-process via `FastMCP.call_tool(...)` against a `tmp_path` substrate — no subprocess, no stdio.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest, **`mcp` 1.28.0 (already installed in the venv)**. FastMCP is `mcp.server.fastmcp.FastMCP`.

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** add `mcp>=1.2` as an **optional** extra `[mcp]` in `pyproject.toml` (runtime core stays `pyyaml`-only; the MCP layer is opt-in). It is already installed in the venv (1.28.0).
- **Thin wrapper, no business logic:** tools call `Service` methods and return their results unchanged. No scheduling, no substrate access, no ledger mutation in this layer — `Service` owns all of that. If a tool needs logic the `Service` doesn't expose, that is a `Service` gap to surface, not logic to add here.
- **The seven tools map 1:1 to Service methods:** `submit_sprint`, `approve_sprint`, `list_sprints`, `get_sprint`, `list_results`, `get_result`, `ledger_status`. No extra tools, no merged tools.
- **Error translation:** a `Service` `NotFoundError` must reach the client as an MCP tool error (`mcp.server.fastmcp.exceptions.ToolError`) with a human-readable message (e.g. `sprint not found: sp9`), never as a raw `KeyError` repr (`"'sp9'"`). `ValueError` from `submit_sprint` (empty/duplicate) likewise becomes a `ToolError` with its message preserved.
- **Returns stay JSON-serialisable:** tools return exactly what `Service` returns (plain dict/list/str). Do not re-wrap or stringify.
- **Backward compatibility:** all existing tests stay green; no behavior change to dispatcher/worker/scheduler/substrate. The only edit to a prior-phase file is the `get_sprint` lease-dict unification in Task 1 (additive).
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only 1b-2b-ii is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 / 1 / 1b-1 / 1b-2a / 1b-2b-i | skeleton; scheduling; job kill; restart reconciliation; service core | DONE |
| **1b-2b-ii — MCP server** (this plan) | wrap `Service` as MCP tools (dep: `mcp`) | **planned here** |
| 1b-2b-iii — HTTP API + container | wrap `Service` as REST (deps: fastapi/uvicorn) + docker compose | after ii |
| 1b-2c — PID-reuse guard | identity token before signalling a stored PID | parallel hardening |

---

## File Structure

```
src/coscience/
  service.py        # MODIFY (Task 1): get_sprint lease dict gains "sprint_id" (unify with ledger_status)
  mcp_server.py     # NEW (Task 2): build_server(service) -> FastMCP + main()
tests/
  test_service_ledger.py       # MODIFY (Task 1): assert get_sprint lease carries sprint_id
  test_service_integration.py  # MODIFY (Task 1): serialisability guard seeds a non-None lease
  test_mcp_server.py           # NEW (Task 2): in-process tool calls over a tmp substrate
  test_mcp_entry.py            # NEW (Task 3): main() resolves repo root + runs server (run monkeypatched)
pyproject.toml      # MODIFY (Task 3): [mcp] optional extra + coscience-mcp console script
```

**Reviewer reference — FastMCP `call_tool` return shapes (verified on mcp 1.28.0):**
- A tool returning a **dict** → `call_tool` returns a `Sequence[ContentBlock]` (a list); the single `TextContent.text` is `json.dumps` of the dict.
- A tool returning a **list** (or a scalar) → `call_tool` returns a **tuple** `(blocks, structured)` where `structured == {"result": <the list/scalar>}`.
- A raising tool → `call_tool` raises `mcp.server.fastmcp.exceptions.ToolError` wrapping the message.
The tests below use one `unwrap()` helper that handles both shapes; it is the source of truth — if a future mcp version changes the shape, fix the helper, not the tools.

---

## Task 1: Unify the `get_sprint` lease dict (carry-over from 1b-2b-i)

**Why:** `ledger_status()` lease dicts include `sprint_id`, but `get_sprint()`'s lease dict omits it — an inconsistency the transport layers would expose to clients. Unify now, before wrapping. Also harden the serialisability guard to actually exercise a populated lease.

**Files:**
- Modify: `src/coscience/service.py` (the `get_sprint` lease dict only)
- Modify: `tests/test_service_ledger.py`, `tests/test_service_integration.py`

**Interface change:** `get_sprint(id)["lease"]`, when non-`None`, gains a `"sprint_id"` key (same value/shape as in `ledger_status`). No other keys change.

- [ ] **Step 1: Write/extend the failing tests**

Add to `tests/test_service_ledger.py`:
```python
def test_get_sprint_lease_includes_sprint_id(tmp_path):
    pool = ResourcePool({"gpu": 2.0})
    led = Ledger(pool, tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)

    svc = Service(tmp_path, pool=pool)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    lease = svc.get_sprint("sp1")["lease"]
    assert lease is not None
    assert lease["sprint_id"] == "sp1"
```
(The `Ledger`, `ResourcePool`, `Service` imports already exist at the top of this file.)

In `tests/test_service_integration.py`, strengthen `test_every_return_value_is_json_serialisable` so the serialised `get_sprint` carries a **non-None** lease (otherwise the lease branch is never exercised). Replace the body with:
```python
def test_every_return_value_is_json_serialisable(tmp_path):
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool

    pool = ResourcePool({"gpu": 2.0})
    led = Ledger(pool, tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)

    svc = Service(tmp_path, pool=pool)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}],
                      resources_required={"gpu": 1})
    svc.approve_sprint("sp1")
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="x"))

    detail = svc.get_sprint("sp1")
    assert detail["lease"]["sprint_id"] == "sp1"  # lease branch is exercised
    # None of these should raise TypeError on json.dumps.
    json.dumps(svc.list_sprints())
    json.dumps(detail)
    json.dumps(svc.list_results())
    json.dumps(svc.get_result("r1"))
    json.dumps(svc.ledger_status())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_ledger.py tests/test_service_integration.py -v`
Expected: the two new/changed assertions fail with `KeyError: 'sprint_id'`.

- [ ] **Step 3: Add `sprint_id` to the `get_sprint` lease dict**

In `src/coscience/service.py`, in `get_sprint`, change the lease dict to include `sprint_id` (mirroring `ledger_status`):
```python
            "lease": None if lease is None else {
                "id": lease.id, "sprint_id": lease.sprint_id, "amounts": lease.amounts,
                "granted_at": lease.granted_at, "expires_at": lease.expires_at,
                "priority": lease.priority, "preemptible": lease.preemptible,
            },
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_ledger.py tests/test_service_integration.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_ledger.py tests/test_service_integration.py
git commit -m "fix: unify get_sprint lease dict with ledger_status (add sprint_id)"
```

---

## Task 2: MCP server — `build_server` + the seven tools

**Files:**
- Create: `src/coscience/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- `build_server(service: Service, name: str = "coscience") -> FastMCP` — returns a `FastMCP` with seven tools registered, each closing over `service`. Tools and signatures:
  - `submit_sprint(id: str, goals: str, plan: list[dict], program: str | None = None, priority: int = 0, preemptible: bool = True, resources_required: dict | None = None) -> dict` — calls `service.submit_sprint(...)` (status defaults to `"proposed"`), then returns `service.get_sprint(id)` (the created detail). `ValueError` (empty/duplicate) → `ToolError`.
  - `approve_sprint(id: str) -> dict` — calls `service.approve_sprint(id)`, returns `service.get_sprint(id)`. Missing → `ToolError`.
  - `list_sprints(status: str | None = None) -> list[dict]` — returns `service.list_sprints(status)`.
  - `get_sprint(id: str) -> dict` — returns `service.get_sprint(id)`. Missing → `ToolError`.
  - `list_results() -> list[dict]` — returns `service.list_results()`.
  - `get_result(id: str) -> dict` — returns `service.get_result(id)`. Missing → `ToolError`.
  - `ledger_status() -> dict` — returns `service.ledger_status()`.
- Error translation helper: wrap `Service` exceptions so the client sees a clean message.
  - `NotFoundError` → `raise ToolError(f"not found: {id}")`.
  - `ValueError` → `raise ToolError(str(exc))`.
- Each tool **must have a one-line docstring** (FastMCP uses it as the tool description the LLM reads). Keep them action-oriented, e.g. `"Submit a new sprint proposal; returns the created sprint detail."`

**Note on `submit_sprint`'s `id` argument:** `Service.submit_sprint` takes `id` as a keyword-only argument. The MCP tool exposes a positional/keyword `id` param and forwards it as `service.submit_sprint(id=id, goals=goals, ...)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_server.py`:
```python
import asyncio
import json

import pytest

from coscience.mcp_server import build_server
from coscience.service import Service


def unwrap(result):
    """Normalise FastMCP call_tool output to the tool's Python return value.

    dict-returning tools -> Sequence[ContentBlock]; parse the single text block.
    list/scalar-returning tools -> (blocks, {"result": <value>}); take structured.
    """
    if isinstance(result, tuple):
        return result[1]["result"]
    return json.loads(result[0].text)


def call(server, name, args):
    return unwrap(asyncio.run(server.call_tool(name, args)))


@pytest.fixture
def server(tmp_path):
    return build_server(Service(tmp_path))


def test_lists_all_seven_tools(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {
        "submit_sprint", "approve_sprint", "list_sprints", "get_sprint",
        "list_results", "get_result", "ledger_status",
    }


def test_every_tool_has_a_description(server):
    for t in asyncio.run(server.list_tools()):
        assert t.description and t.description.strip()


def test_submit_then_get_and_list(server):
    created = call(server, "submit_sprint", {
        "id": "sp1", "goals": "cure", "plan": [{"id": "s1", "run": "echo hi"}],
        "priority": 3, "resources_required": {"gpu": 1},
    })
    assert created["id"] == "sp1"
    assert created["status"] == "proposed"
    assert created["plan"] == [{"id": "s1", "run": "echo hi"}]

    rows = call(server, "list_sprints", {"status": "proposed"})
    assert [r["id"] for r in rows] == ["sp1"]

    detail = call(server, "get_sprint", {"id": "sp1"})
    assert detail["priority"] == 3
    assert detail["lease"] is None


def test_approve_changes_status(server):
    call(server, "submit_sprint", {"id": "sp1", "goals": "g",
                                    "plan": [{"id": "s1", "run": "true"}]})
    approved = call(server, "approve_sprint", {"id": "sp1"})
    assert approved["status"] == "approved"
    assert call(server, "list_sprints", {"status": "proposed"}) == []
    assert [r["id"] for r in call(server, "list_sprints", {"status": "approved"})] == ["sp1"]


def test_results_round_trip(server, tmp_path):
    from coscience.models import Result
    # Reuse the same substrate the server's Service is bound to.
    Service(tmp_path).substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    assert call(server, "list_results", {}) == [{"id": "r1", "sprint": "sp1", "summary": "found X"}]
    assert call(server, "get_result", {"id": "r1"})["summary"] == "found X"


def test_ledger_status_shape(server):
    status = call(server, "ledger_status", {})
    assert set(status) == {"capacity", "used", "available", "leases"}


def test_missing_sprint_raises_tool_error(server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("get_sprint", {"id": "nope"}))


def test_duplicate_submit_raises_tool_error(server):
    from mcp.server.fastmcp.exceptions import ToolError
    call(server, "submit_sprint", {"id": "sp1", "goals": "g",
                                   "plan": [{"id": "s1", "run": "true"}]})
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("submit_sprint", {
            "id": "sp1", "goals": "g", "plan": [{"id": "s1", "run": "true"}]}))
```

**Implementer note (TDD):** `unwrap`/`call` encode the FastMCP return shapes verified on mcp 1.28.0. Run the tests first; if your installed mcp returns a different shape, adjust `unwrap` until the read-back tools pass — do **not** change the tools to match a helper. The error tests assert `ToolError` is raised; if FastMCP surfaces a different exception type for tool errors in your version, assert that type instead (it must still be an MCP tool error, not a raw `KeyError`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.mcp_server'`.

- [ ] **Step 3: Implement `src/coscience/mcp_server.py`**

```python
"""MCP server exposing coscience.service.Service as Model Context Protocol tools.

Thin wrapper: each tool calls one Service method and returns its (already
JSON-serialisable) result. Service NotFoundError/ValueError become ToolError so
clients see a clean message instead of a raw KeyError repr.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from coscience.service import NotFoundError, Service


def build_server(service: Service, name: str = "coscience") -> FastMCP:
    server = FastMCP(name)

    @server.tool()
    def submit_sprint(id: str, goals: str, plan: list[dict],
                      program: str | None = None, priority: int = 0,
                      preemptible: bool = True,
                      resources_required: dict | None = None) -> dict:
        """Submit a new sprint proposal; returns the created sprint detail."""
        try:
            service.submit_sprint(id=id, goals=goals, plan=plan, program=program,
                                  priority=priority, preemptible=preemptible,
                                  resources_required=resources_required)
        except ValueError as exc:
            raise ToolError(str(exc))
        return service.get_sprint(id)

    @server.tool()
    def approve_sprint(id: str) -> dict:
        """Approve a proposed sprint; returns the updated sprint detail."""
        try:
            service.approve_sprint(id)
        except NotFoundError:
            raise ToolError(f"sprint not found: {id}")
        return service.get_sprint(id)

    @server.tool()
    def list_sprints(status: str | None = None) -> list[dict]:
        """List sprints, optionally filtered by status (proposed/approved/...)."""
        return service.list_sprints(status)

    @server.tool()
    def get_sprint(id: str) -> dict:
        """Get full detail for one sprint, including progress and any lease."""
        try:
            return service.get_sprint(id)
        except NotFoundError:
            raise ToolError(f"sprint not found: {id}")

    @server.tool()
    def list_results() -> list[dict]:
        """List all recorded results (id, sprint, summary)."""
        return service.list_results()

    @server.tool()
    def get_result(id: str) -> dict:
        """Get one result by id."""
        try:
            return service.get_result(id)
        except NotFoundError:
            raise ToolError(f"result not found: {id}")

    @server.tool()
    def ledger_status() -> dict:
        """Current resource ledger: capacity, used, available, and active leases."""
        return service.ledger_status()

    return server


def _service_from_env() -> Service:
    repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))
    return Service(repo_root)


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    build_server(_service_from_env()).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP server wrapping Service (seven tools)"
```

---

## Task 3: Packaging + entry point

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_mcp_entry.py`

**Interfaces:**
- `pyproject.toml`: add optional extra and console script:
  - `[project.optional-dependencies]` gains `mcp = ["mcp>=1.2"]` (keep the existing `dev = ["pytest>=8"]`).
  - `[project.scripts]` gains `coscience-mcp = "coscience.mcp_server:main"` (keep the existing `coscience = "coscience.cli:main"`).
- `main()` and `_service_from_env()` already exist from Task 2; this task proves them and packages them. If Task 2's `main()` differs from the plan, reconcile here.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_entry.py`:
```python
import coscience.mcp_server as mcp_server
from coscience.service import Service


def test_service_from_env_uses_repo_var(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    svc = mcp_server._service_from_env()
    assert isinstance(svc, Service)
    assert svc.repo_root == tmp_path


def test_service_from_env_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_REPO", raising=False)
    monkeypatch.chdir(tmp_path)
    assert mcp_server._service_from_env().repo_root == tmp_path


def test_main_builds_and_runs_server(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    ran = {}

    def fake_run(self, *args, **kwargs):
        ran["yes"] = True

    monkeypatch.setattr("mcp.server.fastmcp.FastMCP.run", fake_run)
    mcp_server.main()
    assert ran.get("yes") is True
```

- [ ] **Step 2: Run the tests to verify they fail (or pass on the env helpers)**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_mcp_entry.py -v`
Expected: the env-helper tests may already pass (code from Task 2); `test_main_builds_and_runs_server` passes once `main()` exists. If any fail, reconcile `main()`/`_service_from_env()` to match the interfaces above.

- [ ] **Step 3: Update `pyproject.toml`**

```toml
[project.optional-dependencies]
dev = ["pytest>=8"]
mcp = ["mcp>=1.2"]

[project.scripts]
coscience = "coscience.cli:main"
coscience-mcp = "coscience.mcp_server:main"
```

- [ ] **Step 4: Run the entry tests, then the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_mcp_entry.py -v`
Expected: 3 passed.

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass (prior 101 + Task 1/2/3 additions). Record the count.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_mcp_entry.py
git commit -m "feat: coscience-mcp console script + [mcp] optional extra"
```

---

## Self-Review

**Spec coverage:**
- get_sprint/ledger_status lease parity + exercised serialisability guard → Task 1 ✓
- seven MCP tools 1:1 over Service, error translation, docstrings → Task 2 ✓
- packaging (extra + console script) + entry point proof → Task 3 ✓
- thin wrapper, no business logic, JSON-serialisable passthrough → Global Constraints + Task 2 ✓
- Explicitly deferred: HTTP transport + container (iii), PID-reuse guard (1b-2c). No streaming/SSE transport (stdio only here).

**Placeholder scan:** complete code in every step; real assertions; no TBD. ✓

**Type consistency:** `build_server(service: Service, name="coscience") -> FastMCP`; tools named exactly `submit_sprint/approve_sprint/list_sprints/get_sprint/list_results/get_result/ledger_status`; `main()` + `_service_from_env()`; `ToolError` from `mcp.server.fastmcp.exceptions`. ✓

**Known simplifications (intentional):**
- Stdio transport only; SSE/streamable-http transports are a later concern (the HTTP API in iii covers networked access).
- `submit_sprint`/`approve_sprint` tools return the full sprint detail (round-tripped through `get_sprint`) rather than a bare id, so the calling agent immediately sees the resulting state.
- No auth on the MCP server — stdio is local-process; networked auth is part of the HTTP/container increment.
