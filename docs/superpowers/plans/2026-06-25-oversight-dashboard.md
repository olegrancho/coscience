# Oversight Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the oversight committee's control surface — new tested `Service` mutations + JSON endpoints (reject/edit sprint, pause/resume/close program, program guidance, human→PM steer), then a React+Mantine SPA over them.

**Architecture:** The `Service` is the single seam; correctness lives in tested Python and the SPA is a thin consumer. Backend increments (Tasks 1–14) ship green and fully usable via `/api/*` before any frontend exists; the SPA (Tasks 15–21) consumes the same JSON API. Spec: `docs/superpowers/specs/2026-06-25-dashboard-design.md`.

**Tech Stack:** Python 3.11+, FastAPI, pydantic, pytest (backend); React 18 + TypeScript + Vite + Mantine + TanStack Query + React Router + vitest (frontend).

## Global Constraints

- Test command: `/home/oleg/venvs/coscience/bin/python -m pytest` (run from repo root).
- The full suite is green at 177 tests on the parent commit; never reduce the count — every task is additive or repoints existing tests.
- All JSON routes live under `/api/*` (Task 1 onward). `GET /api/health` is the liveness route.
- Error mapping is fixed and uniform: `NotFoundError → 404`; status-guard / validation `ValueError → 422`; duplicate-id `ValueError → 409`.
- `build_app(service, title=...)` stays API-only and import-light (must NOT import `mcp`); SPA static serving is added only in `create_app()`, so route tests never need a built bundle.
- Reject means `proposed → canceled` (reuse the existing `SprintStatus.CANCELED`); never add a new status value.
- Edit guards: `goals`/`plan` editable only while `proposed`; `priority`/`resources_required`/`preemptible` editable while `proposed`/`approved`/`executing`; `done`/`canceled` fully read-only.
- Guidance is read-only PM input: never written by the PM, never staged; `pm_beat`/staging/idempotency are untouched.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Frontend lives under `frontend/`; the SPA talks only to `/api/*`; backend remains the correctness authority and the UI mirrors its guards.

---

## File Structure

**Backend (modify):**
- `src/coscience/service.py` — add `reject_sprint`, `edit_sprint`, `set_program_status`, `add_guidance`, `remove_guidance`, `list_guidance`.
- `src/coscience/substrate.py` — add `load_guidance`, `save_guidance`.
- `src/coscience/http_api.py` — move routes under `/api` (APIRouter prefix); add reject/patch/status/guidance routes; add SPA static mount in `create_app()`.
- `src/coscience/pm_reasoner.py` — add `PMContext.human_guidance` field.
- `src/coscience/pm_agent.py` — `gather_context` populates `human_guidance`.
- `src/coscience/pm_claude.py` — `render_prompt` embeds guidance.
- `Dockerfile` — add a Node build stage emitting `frontend/dist`, copy it into the image.

**Backend (test):** `tests/test_service_sprints.py`, `tests/test_service_programs.py`, `tests/test_service_guidance.py` (new), `tests/test_http_api.py`, `tests/test_transport_programs.py`, `tests/test_http_guidance.py` (new), `tests/test_pm_context.py`, `tests/test_pm_claude.py`, `tests/test_container_files.py`.

**Frontend (create) under `frontend/`:** `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/main.tsx`, `src/App.tsx`, `src/api.ts`, `src/sprintActions.ts`, `src/views/ProgramsOverview.tsx`, `src/views/ProgramDetail.tsx`, `src/views/SprintDetail.tsx`, `src/views/ResultDetail.tsx`, `src/views/Ledger.tsx`, `src/components/SprintEditModal.tsx`, `src/components/ProposeSprintModal.tsx`, plus tests `src/api.test.ts`, `src/sprintActions.test.ts`.

**Docs:** `docs/superpowers/plans/2026-06-25-dashboard-acceptance.md` (runbook).

---

## Task 1: Move the JSON API under `/api`

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_api.py`, `tests/test_transport_programs.py`

**Interfaces:**
- Consumes: existing `build_app(service, title=...)`, `Service`, `service_from_env`.
- Produces: every route now mounted under the `/api` prefix; `build_app` signature unchanged.

- [ ] **Step 1: Repoint the existing HTTP tests to `/api`**

In `tests/test_http_api.py` prefix every request path with `/api` (e.g. `client.get("/health")` → `client.get("/api/health")`, `client.post("/sprints", ...)` → `client.post("/api/sprints", ...)`, and the `/sprints/sp1`, `/sprints/sp1/approve`, `/results`, `/results/r1`, `/ledger` paths likewise). In `tests/test_transport_programs.py` prefix the HTTP-side paths in `test_http_list_and_get_program` and `test_http_invalid_status_is_422` (`/programs` → `/api/programs`, `/programs/p1` → `/api/programs/p1`, `/programs/nope` → `/api/programs/nope`). Leave the MCP-side tests untouched.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py tests/test_transport_programs.py -q`
Expected: FAIL — old unprefixed routes 404 / new `/api` routes not yet mounted (404).

- [ ] **Step 3: Mount all routes on an `/api` router**

In `src/coscience/http_api.py`, change `build_app` to register routes on an `APIRouter(prefix="/api")` and include it. Add the import `from fastapi import APIRouter` and replace the body so it reads:

```python
def build_app(service: Service, title: str = "Co-Science Platform") -> FastAPI:
    app = FastAPI(title=title, version="0.0.0")
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # ... move every existing @app.<verb>("/<path>") to @api.<verb>("/<path>")
    #     keeping each path string the SAME (the prefix adds /api):
    #     "/sprints", "/sprints/{sprint_id}", "/sprints/{sprint_id}/approve",
    #     "/results", "/results/{result_id}", "/ledger",
    #     "/programs", "/programs/{program_id}"

    app.include_router(api)
    return app
```

Mechanically rename each `@app.` decorator in the current body to `@api.`; do not change path strings, handler bodies, or status codes. Keep `create_app()` and `main()` as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py tests/test_transport_programs.py tests/test_http_entry.py -q`
Expected: PASS.

- [ ] **Step 5: Repoint the PM acceptance runbook**

In `docs/superpowers/plans/phase2b-pm-acceptance.md`, update any HTTP URLs that reference unprefixed routes (e.g. `localhost:8000/sprints`, `/programs`, `/docs`) to the `/api` prefix (`/api/sprints`, `/api/programs`). `/docs` (Swagger) stays at `/docs` — FastAPI keeps it at root; only the data routes move.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_api.py tests/test_transport_programs.py docs/superpowers/plans/phase2b-pm-acceptance.md
git commit -m "refactor: mount JSON API under /api prefix

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `Service.reject_sprint`

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_sprints.py`

**Interfaces:**
- Consumes: `Service._load_sprint(sprint_id) -> Sprint` (raises `NotFoundError`), `SprintStatus`, `substrate.save_sprint`.
- Produces: `reject_sprint(self, sprint_id: str) -> None` — `proposed → canceled`; raises `ValueError` if not currently `proposed`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service_sprints.py`:

```python
from coscience.models import SprintStatus


def test_reject_moves_proposed_to_canceled(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    svc.reject_sprint("sp1")
    assert svc.get_sprint("sp1")["status"] == "canceled"


def test_reject_non_proposed_raises(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    svc.approve_sprint("sp1")
    with pytest.raises(ValueError):
        svc.reject_sprint("sp1")


def test_reject_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).reject_sprint("nope")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_sprints.py -q`
Expected: FAIL — `Service` has no attribute `reject_sprint`.

- [ ] **Step 3: Implement `reject_sprint`**

In `src/coscience/service.py`, add after `approve_sprint`:

```python
    def reject_sprint(self, sprint_id: str) -> None:
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PROPOSED:
            raise ValueError(f"can only reject a proposed sprint; {sprint_id} is {sprint.status.value}")
        sprint.status = SprintStatus.CANCELED
        self.substrate.save_sprint(sprint)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_sprints.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_sprints.py
git commit -m "feat: Service.reject_sprint (proposed -> canceled)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `Service.edit_sprint`

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_sprints.py`

**Interfaces:**
- Consumes: `Service._load_sprint`, `SprintStatus`, `Step.from_dict`, `substrate.save_sprint`.
- Produces: `edit_sprint(self, sprint_id, *, goals=None, plan=None, priority=None, resources_required=None, preemptible=None) -> None`. Partial patch (only provided kwargs change). Guards per Global Constraints. `plan`, if provided, is `list[dict]` of `{id, run}` and must be non-empty.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service_sprints.py`:

```python
def _executing(svc, sid):
    # Force a sprint into EXECUTING for guard tests (no scheduler needed).
    s = svc.substrate.load_sprint(sid)
    s.status = SprintStatus.EXECUTING
    svc.substrate.save_sprint(s)


def test_edit_proposed_all_fields(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="old", plan=[{"id": "s1", "run": "a"}])
    svc.edit_sprint("sp1", goals="new", plan=[{"id": "s2", "run": "b"}],
                    priority=5, resources_required={"gpu": 2}, preemptible=False)
    d = svc.get_sprint("sp1")
    assert d["goals"] == "new"
    assert d["plan"] == [{"id": "s2", "run": "b"}]
    assert d["priority"] == 5
    assert d["resources_required"] == {"gpu": 2.0}
    assert d["preemptible"] is False


def test_edit_partial_leaves_other_fields(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="keep", plan=[{"id": "s1", "run": "a"}], priority=1)
    svc.edit_sprint("sp1", priority=9)
    d = svc.get_sprint("sp1")
    assert d["goals"] == "keep"
    assert d["priority"] == 9


def test_edit_goals_blocked_when_not_proposed(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    svc.approve_sprint("sp1")
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", goals="nope")


def test_edit_priority_allowed_when_executing(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    _executing(svc, "sp1")
    svc.edit_sprint("sp1", priority=7)
    assert svc.get_sprint("sp1")["priority"] == 7


def test_edit_blocked_when_done(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    s = svc.substrate.load_sprint("sp1")
    s.status = SprintStatus.DONE
    svc.substrate.save_sprint(s)
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", priority=3)


def test_edit_empty_plan_raises(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", plan=[])


def test_edit_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).edit_sprint("nope", priority=1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_sprints.py -q`
Expected: FAIL — no attribute `edit_sprint`.

- [ ] **Step 3: Implement `edit_sprint`**

In `src/coscience/service.py`, add after `reject_sprint`:

```python
    def edit_sprint(self, sprint_id: str, *, goals=None, plan=None, priority=None,
                    resources_required=None, preemptible=None) -> None:
        sprint = self._load_sprint(sprint_id)
        st = sprint.status
        if st in (SprintStatus.DONE, SprintStatus.CANCELED):
            raise ValueError(f"{sprint_id} is {st.value} and is read-only")
        if (goals is not None or plan is not None) and st != SprintStatus.PROPOSED:
            raise ValueError("goals/plan are editable only while proposed")
        if plan is not None and len(plan) == 0:
            raise ValueError("plan must have at least one step")
        if goals is not None:
            sprint.goals = goals
        if plan is not None:
            sprint.plan = [Step.from_dict(s) for s in plan]
        if priority is not None:
            sprint.priority = priority
        if resources_required is not None:
            sprint.resources_required = {k: float(v) for k, v in resources_required.items()}
        if preemptible is not None:
            sprint.preemptible = preemptible
        self.substrate.save_sprint(sprint)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_sprints.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_sprints.py
git commit -m "feat: Service.edit_sprint with status-gated fields

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Sprint mutation HTTP routes (reject + patch)

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_api.py`

**Interfaces:**
- Consumes: `service.reject_sprint`, `service.edit_sprint`, `service.get_sprint`, existing `StepIn`.
- Produces: `POST /api/sprints/{id}/reject` and `PATCH /api/sprints/{id}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_http_api.py`:

```python
def test_reject_via_http(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": [{"id": "s1", "run": "true"}]})
    r = client.post("/api/sprints/sp1/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "canceled"


def test_reject_non_proposed_is_422(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": [{"id": "s1", "run": "true"}]})
    client.post("/api/sprints/sp1/approve")
    assert client.post("/api/sprints/sp1/reject").status_code == 422


def test_reject_missing_is_404(client):
    assert client.post("/api/sprints/nope/reject").status_code == 404


def test_patch_priority(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": [{"id": "s1", "run": "true"}]})
    r = client.patch("/api/sprints/sp1", json={"priority": 8})
    assert r.status_code == 200
    assert r.json()["priority"] == 8


def test_patch_goals_when_approved_is_422(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": [{"id": "s1", "run": "true"}]})
    client.post("/api/sprints/sp1/approve")
    assert client.patch("/api/sprints/sp1", json={"goals": "x"}).status_code == 422


def test_patch_missing_is_404(client):
    assert client.patch("/api/sprints/nope", json={"priority": 1}).status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py -q`
Expected: FAIL — routes not found (404/405).

- [ ] **Step 3: Implement the routes**

In `src/coscience/http_api.py`, add a patch body model near `SprintSubmit`:

```python
class SprintPatch(BaseModel):
    goals: str | None = None
    plan: list[StepIn] | None = None
    priority: int | None = None
    resources_required: dict[str, float] | None = None
    preemptible: bool | None = None
```

Add these routes inside `build_app` (on `api`):

```python
    @api.post("/sprints/{sprint_id}/reject")
    def reject_sprint(sprint_id: str) -> dict:
        try:
            service.reject_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.patch("/sprints/{sprint_id}")
    def patch_sprint(sprint_id: str, body: SprintPatch) -> dict:
        fields = body.model_dump(exclude_unset=True)
        if "plan" in fields and fields["plan"] is not None:
            fields["plan"] = [s if isinstance(s, dict) else s.model_dump() for s in body.plan]
        try:
            service.edit_sprint(sprint_id, **fields)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_api.py
git commit -m "feat: HTTP reject + patch sprint routes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `Service.set_program_status`

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_programs.py`

**Interfaces:**
- Consumes: `ProgramStatus`, `substrate.load_program`, `substrate.save_program`, `substrate.program_dir`.
- Produces: `set_program_status(self, program_id: str, status: str) -> None`. Raises `NotFoundError` if the program is missing, `ValueError` for an invalid status string.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service_programs.py` (it already imports `Service`; add the others it needs):

```python
import pytest
from coscience.models import Program
from coscience.service import NotFoundError
from coscience.pm_runner import pm_run_once
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def test_set_program_status_pause_resume(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    svc.set_program_status("p1", "paused")
    assert svc.get_program("p1")["status"] == "paused"
    svc.set_program_status("p1", "active")
    assert svc.get_program("p1")["status"] == "active"


def test_set_program_status_invalid_raises(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    with pytest.raises(ValueError):
        svc.set_program_status("p1", "bogus")


def test_set_program_status_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).set_program_status("nope", "paused")


def test_paused_program_is_skipped_by_pm(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    svc.set_program_status("p1", "paused")
    reasoner = FakeReasoner([PMCycleOutput(
        proposals=[ProposedSprint(suffix="x", goals="go", plan=[{"id": "s", "run": "true"}])])])
    summaries = pm_run_once(svc.substrate, reasoner)
    assert summaries == []           # paused program not beaten
    assert reasoner.calls == []      # reasoner never consulted
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_programs.py -q`
Expected: FAIL — no attribute `set_program_status`.

- [ ] **Step 3: Implement `set_program_status`**

In `src/coscience/service.py`, add in the programs section:

```python
    def set_program_status(self, program_id: str, status: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        new_status = ProgramStatus(status)  # raises ValueError on a bad value
        program = self.substrate.load_program(program_id)
        program.status = new_status
        self.substrate.save_program(program)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_programs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_programs.py
git commit -m "feat: Service.set_program_status (pause/resume/close)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Program status HTTP route

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_api.py`

**Interfaces:**
- Consumes: `service.set_program_status`, `service.get_program`.
- Produces: `POST /api/programs/{id}/status` with body `{"status": "..."}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_http_api.py`:

```python
from coscience.models import Program


def test_set_program_status_via_http(client):
    client.svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    r = client.post("/api/programs/p1/status", json={"status": "paused"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"


def test_set_program_status_invalid_is_422(client):
    client.svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    assert client.post("/api/programs/p1/status", json={"status": "bogus"}).status_code == 422


def test_set_program_status_missing_is_404(client):
    assert client.post("/api/programs/nope/status", json={"status": "paused"}).status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py -q`
Expected: FAIL — route not found.

- [ ] **Step 3: Implement the route**

In `src/coscience/http_api.py`, add a body model near `SprintPatch`:

```python
class ProgramStatusIn(BaseModel):
    status: str
```

Add the route inside `build_app` (on `api`):

```python
    @api.post("/programs/{program_id}/status")
    def set_program_status(program_id: str, body: ProgramStatusIn) -> dict:
        try:
            service.set_program_status(program_id, body.status)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_program(program_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_api.py
git commit -m "feat: HTTP set program status route

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Substrate guidance storage

**Files:**
- Modify: `src/coscience/substrate.py`
- Test: `tests/test_substrate.py`

**Interfaces:**
- Consumes: `parse`, `serialize` from `coscience.frontmatter_io`; `Substrate.program_dir`.
- Produces: `load_guidance(self, program_id) -> list[dict]` (each note `{"id","text","added_at"}`), `save_guidance(self, program_id, notes: list[dict]) -> None`. Missing file ⇒ `[]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_substrate.py`:

```python
def test_guidance_round_trip(tmp_path):
    from coscience.substrate import Substrate
    sub = Substrate(tmp_path)
    assert sub.load_guidance("p1") == []
    notes = [{"id": "a1", "text": "focus on assays", "added_at": 1.0}]
    sub.save_guidance("p1", notes)
    assert sub.load_guidance("p1") == notes
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_substrate.py -q`
Expected: FAIL — no attribute `load_guidance`.

- [ ] **Step 3: Implement guidance storage**

In `src/coscience/substrate.py`, add in the programs section (after `save_pm_state`):

```python
    def load_guidance(self, program_id: str) -> list[dict]:
        path = self.program_dir(program_id) / "guidance.md"
        if not path.is_file():
            return []
        fm, _ = parse(path.read_text())
        out = []
        for n in fm.get("notes", []):
            out.append({"id": str(n["id"]), "text": str(n["text"]),
                        "added_at": float(n["added_at"])})
        return out

    def save_guidance(self, program_id: str, notes: list[dict]) -> None:
        d = self.program_dir(program_id)
        d.mkdir(parents=True, exist_ok=True)
        fm = {"type": "guidance", "notes": notes}
        (d / "guidance.md").write_text(serialize(fm, f"# Guidance {program_id}\n"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_substrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/substrate.py tests/test_substrate.py
git commit -m "feat: substrate guidance storage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: `Service` guidance CRUD

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_guidance.py` (new)

**Interfaces:**
- Consumes: `substrate.load_guidance`, `substrate.save_guidance`, `substrate.program_dir`, `NotFoundError`.
- Produces: `add_guidance(self, program_id, text) -> dict` (returns the new note `{"id","text","added_at"}`); `list_guidance(self, program_id) -> list[dict]`; `remove_guidance(self, program_id, note_id) -> None` (idempotent for an unknown id; `NotFoundError` for a missing program).

- [ ] **Step 1: Write the failing test**

Create `tests/test_service_guidance.py`:

```python
import pytest

from coscience.models import Program
from coscience.service import NotFoundError, Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    return svc


def test_add_then_list(tmp_path):
    svc = _svc(tmp_path)
    note = svc.add_guidance("p1", "focus on assays")
    assert note["text"] == "focus on assays"
    assert note["id"]
    assert isinstance(note["added_at"], float)
    assert svc.list_guidance("p1") == [note]


def test_remove_one_note(tmp_path):
    svc = _svc(tmp_path)
    a = svc.add_guidance("p1", "alpha")
    b = svc.add_guidance("p1", "beta")
    svc.remove_guidance("p1", a["id"])
    assert svc.list_guidance("p1") == [b]


def test_remove_unknown_id_is_noop(tmp_path):
    svc = _svc(tmp_path)
    a = svc.add_guidance("p1", "alpha")
    svc.remove_guidance("p1", "does-not-exist")
    assert svc.list_guidance("p1") == [a]


def test_guidance_missing_program_raises(tmp_path):
    svc = Service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.add_guidance("nope", "x")
    with pytest.raises(NotFoundError):
        svc.list_guidance("nope")
    with pytest.raises(NotFoundError):
        svc.remove_guidance("nope", "x")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_guidance.py -q`
Expected: FAIL — no attribute `add_guidance`.

- [ ] **Step 3: Implement guidance CRUD**

In `src/coscience/service.py`, add `import time` and `from uuid import uuid4` at the top (if not present), then add in the programs section:

```python
    def _require_program(self, program_id: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)

    def list_guidance(self, program_id: str) -> list[dict]:
        self._require_program(program_id)
        return self.substrate.load_guidance(program_id)

    def add_guidance(self, program_id: str, text: str) -> dict:
        self._require_program(program_id)
        notes = self.substrate.load_guidance(program_id)
        note = {"id": uuid4().hex[:8], "text": text, "added_at": time.time()}
        notes.append(note)
        self.substrate.save_guidance(program_id, notes)
        return note

    def remove_guidance(self, program_id: str, note_id: str) -> None:
        self._require_program(program_id)
        notes = [n for n in self.substrate.load_guidance(program_id) if n["id"] != note_id]
        self.substrate.save_guidance(program_id, notes)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_service_guidance.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_guidance.py
git commit -m "feat: Service guidance CRUD

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Guidance HTTP routes

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_guidance.py` (new)

**Interfaces:**
- Consumes: `service.list_guidance`, `service.add_guidance`, `service.remove_guidance`.
- Produces: `GET /api/programs/{id}/guidance`, `POST /api/programs/{id}/guidance` (201, body `{"text": "..."}`, returns the note), `DELETE /api/programs/{id}/guidance/{note_id}` (204 in both deleted and already-absent cases).

- [ ] **Step 1: Write the failing test**

Create `tests/test_http_guidance.py`:

```python
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    c = TestClient(build_app(svc))
    c.svc = svc
    return c


def test_add_list_delete_guidance(client):
    r = client.post("/api/programs/p1/guidance", json={"text": "focus on assays"})
    assert r.status_code == 201
    note = r.json()
    assert note["text"] == "focus on assays"

    r = client.get("/api/programs/p1/guidance")
    assert [n["id"] for n in r.json()] == [note["id"]]

    assert client.delete(f"/api/programs/p1/guidance/{note['id']}").status_code == 204
    assert client.get("/api/programs/p1/guidance").json() == []


def test_delete_unknown_note_is_204(client):
    assert client.delete("/api/programs/p1/guidance/nope").status_code == 204


def test_guidance_missing_program_is_404(client):
    assert client.get("/api/programs/nope/guidance").status_code == 404
    assert client.post("/api/programs/nope/guidance", json={"text": "x"}).status_code == 404
    assert client.delete("/api/programs/nope/guidance/x").status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_guidance.py -q`
Expected: FAIL — routes not found.

- [ ] **Step 3: Implement the routes**

In `src/coscience/http_api.py`, add `from fastapi import Response` to the fastapi import line, add a body model near `ProgramStatusIn`:

```python
class GuidanceIn(BaseModel):
    text: str
```

Add the routes inside `build_app` (on `api`):

```python
    @api.get("/programs/{program_id}/guidance")
    def list_guidance(program_id: str) -> list[dict]:
        try:
            return service.list_guidance(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/guidance", status_code=201)
    def add_guidance(program_id: str, body: GuidanceIn) -> dict:
        try:
            return service.add_guidance(program_id, body.text)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.delete("/programs/{program_id}/guidance/{note_id}", status_code=204)
    def remove_guidance(program_id: str, note_id: str) -> Response:
        try:
            service.remove_guidance(program_id, note_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        return Response(status_code=204)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_guidance.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_guidance.py
git commit -m "feat: HTTP guidance routes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: PM steer — guidance into the reasoner context

**Files:**
- Modify: `src/coscience/pm_reasoner.py`, `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py`
- Test: `tests/test_pm_context.py`, `tests/test_pm_claude.py`

**Interfaces:**
- Consumes: `substrate.load_guidance`, existing `PMContext`, `gather_context`, `render_prompt`.
- Produces: `PMContext.human_guidance: list[str]` (default empty); `gather_context` populates it from the program's guidance notes; `render_prompt` embeds it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pm_context.py`:

```python
def test_gather_context_includes_human_guidance(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    sub.save_guidance("p1", [{"id": "a", "text": "focus on assays", "added_at": 1.0},
                             {"id": "b", "text": "avoid mice", "added_at": 2.0}])
    ctx = gather_context(sub, "p1")
    assert ctx.human_guidance == ["focus on assays", "avoid mice"]


def test_gather_context_empty_guidance(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    assert gather_context(sub, "p1").human_guidance == []
```

Append to `tests/test_pm_claude.py`:

```python
def test_render_prompt_includes_guidance():
    from coscience.pm_claude import render_prompt
    from coscience.pm_reasoner import PMContext
    ctx = PMContext(program_id="p1", goals="g", cycle=0,
                    human_guidance=["focus on assays"])
    assert "focus on assays" in render_prompt(ctx)


def test_render_prompt_omits_guidance_when_empty():
    from coscience.pm_claude import render_prompt
    from coscience.pm_reasoner import PMContext
    ctx = PMContext(program_id="p1", goals="g", cycle=0)
    assert "HUMAN GUIDANCE" not in render_prompt(ctx)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_context.py tests/test_pm_claude.py -q`
Expected: FAIL — `PMContext` has no `human_guidance` / prompt lacks guidance.

- [ ] **Step 3: Add the field**

In `src/coscience/pm_reasoner.py`, add to `PMContext` (after `prior_proposals`):

```python
    human_guidance: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Populate it in `gather_context`**

In `src/coscience/pm_agent.py`, in `gather_context`, before the `return PMContext(...)`, add:

```python
    guidance = [n["text"] for n in substrate.load_guidance(program_id)]
```

and add `human_guidance=guidance,` to the `PMContext(...)` constructor call.

- [ ] **Step 5: Embed it in `render_prompt`**

In `src/coscience/pm_claude.py`, inside `render_prompt`, before the final return, build a guidance block:

```python
    guidance_block = ""
    if context.human_guidance:
        notes = "\n".join(f"- {g}" for g in context.human_guidance)
        guidance_block = (
            "\n\nHUMAN GUIDANCE (standing direction from the oversight committee "
            "— weigh these in your proposals):\n" + notes)
```

and insert `{guidance_block}` into the f-string immediately after the `PROGRAM GOALS:\n{context.goals}` block (before the `OPEN SPRINTS` line).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_context.py tests/test_pm_claude.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest -q`
Expected: PASS (all prior tests + the new ones; the backend layer is complete).

- [ ] **Step 8: Commit**

```bash
git add src/coscience/pm_reasoner.py src/coscience/pm_agent.py src/coscience/pm_claude.py tests/test_pm_context.py tests/test_pm_claude.py
git commit -m "feat: human guidance flows into PM reasoner context

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Frontend scaffold (Vite + React + Mantine + TS)

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Test: (build smoke only this task)

**Interfaces:**
- Produces: a buildable SPA shell with Mantine + a QueryClient + a Router; `npm run build` emits `frontend/dist`.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "coscience-dashboard",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "@mantine/core": "^7.11.0",
    "@mantine/hooks": "^7.11.0",
    "@tanstack/react-query": "^5.51.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "react-router-dom": "^6.25.0"
  },
  "devDependencies": {
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.3",
    "vite": "^5.3.4",
    "vitest": "^2.0.4"
  }
}
```

- [ ] **Step 2: Create `frontend/vite.config.ts`**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: { environment: "jsdom", globals: true },
});
```

- [ ] **Step 3: Create `frontend/tsconfig.json` and `frontend/tsconfig.node.json`**

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "types": ["vitest/globals"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 4: Create `frontend/index.html` and `frontend/src/main.tsx`**

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Co-Science — Oversight</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import "@mantine/core/styles.css";
import App from "./App";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchInterval: 10000, refetchOnWindowFocus: true } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider defaultColorScheme="auto">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
```

- [ ] **Step 5: Create `frontend/src/App.tsx` (routing shell)**

```tsx
import { AppShell, Group, Title, Anchor } from "@mantine/core";
import { Link, Route, Routes } from "react-router-dom";
import ProgramsOverview from "./views/ProgramsOverview";
import ProgramDetail from "./views/ProgramDetail";
import SprintDetail from "./views/SprintDetail";
import ResultDetail from "./views/ResultDetail";
import Ledger from "./views/Ledger";

export default function App() {
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Anchor component={Link} to="/" underline="never">
            <Title order={4}>Co-Science — Oversight</Title>
          </Anchor>
          <Anchor component={Link} to="/ledger">Ledger</Anchor>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Routes>
          <Route path="/" element={<ProgramsOverview />} />
          <Route path="/programs/:id" element={<ProgramDetail />} />
          <Route path="/sprints/:id" element={<SprintDetail />} />
          <Route path="/results/:id" element={<ResultDetail />} />
          <Route path="/ledger" element={<Ledger />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
```

- [ ] **Step 6: Install and verify the build**

Run: `cd frontend && npm install`
Then create empty placeholder view files so the build resolves (each: `export default function X(){return null}`) — they are fully implemented in Tasks 13–16, but the build must pass now:

```bash
mkdir -p src/views
printf 'export default function ProgramsOverview(){return null}\n' > src/views/ProgramsOverview.tsx
printf 'export default function ProgramDetail(){return null}\n' > src/views/ProgramDetail.tsx
printf 'export default function SprintDetail(){return null}\n' > src/views/SprintDetail.tsx
printf 'export default function ResultDetail(){return null}\n' > src/views/ResultDetail.tsx
printf 'export default function Ledger(){return null}\n' > src/views/Ledger.tsx
```

Run: `npm run build`
Expected: build succeeds, `frontend/dist/index.html` exists.

- [ ] **Step 7: Add a `.gitignore` for frontend artifacts**

Create `frontend/.gitignore`:

```
node_modules/
dist/
```

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tsconfig.json frontend/tsconfig.node.json frontend/index.html frontend/.gitignore frontend/src
git commit -m "feat: SPA scaffold (vite/react/mantine)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: Typed API client + sprint-action logic (unit tested)

**Files:**
- Create: `frontend/src/api.ts`, `frontend/src/sprintActions.ts`, `frontend/src/api.test.ts`, `frontend/src/sprintActions.test.ts`

**Interfaces:**
- Produces: a typed `api` object wrapping every `/api/*` call the SPA needs; `availableActions(status)` and `editableFields(status)` — the UI's mirror of the backend guards.

- [ ] **Step 1: Write the failing `sprintActions` test**

Create `frontend/src/sprintActions.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { availableActions, editableFields } from "./sprintActions";

describe("availableActions", () => {
  it("offers approve+reject+edit for proposed", () => {
    expect(availableActions("proposed").sort()).toEqual(["approve", "edit", "reject"]);
  });
  it("offers only edit for approved/executing", () => {
    expect(availableActions("approved")).toEqual(["edit"]);
    expect(availableActions("executing")).toEqual(["edit"]);
  });
  it("offers nothing for done/canceled", () => {
    expect(availableActions("done")).toEqual([]);
    expect(availableActions("canceled")).toEqual([]);
  });
});

describe("editableFields", () => {
  it("allows all fields when proposed", () => {
    expect(editableFields("proposed")).toEqual(
      { goals: true, plan: true, priority: true, resources: true, preemptible: true });
  });
  it("allows only scheduler fields when approved/executing", () => {
    expect(editableFields("executing")).toEqual(
      { goals: false, plan: false, priority: true, resources: true, preemptible: true });
  });
  it("allows nothing when done", () => {
    expect(editableFields("done")).toEqual(
      { goals: false, plan: false, priority: false, resources: false, preemptible: false });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/sprintActions.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `frontend/src/sprintActions.ts`**

```ts
export type SprintStatus = "proposed" | "approved" | "executing" | "done" | "canceled";
export type Action = "approve" | "reject" | "edit";

export function availableActions(status: SprintStatus): Action[] {
  if (status === "proposed") return ["approve", "reject", "edit"];
  if (status === "approved" || status === "executing") return ["edit"];
  return [];
}

export interface EditableFields {
  goals: boolean; plan: boolean; priority: boolean;
  resources: boolean; preemptible: boolean;
}

export function editableFields(status: SprintStatus): EditableFields {
  const proposed = status === "proposed";
  const scheduler = proposed || status === "approved" || status === "executing";
  return { goals: proposed, plan: proposed, priority: scheduler,
           resources: scheduler, preemptible: scheduler };
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/sprintActions.test.ts`
Expected: PASS.

- [ ] **Step 5: Implement `frontend/src/api.ts`**

```ts
export interface ProgramRow { id: string; title: string; status: string; goals: string }
export interface SprintRef { id: string; status: string; goals: string }
export interface Program extends ProgramRow {
  report: string; cycle: number; sprints: SprintRef[];
}
export interface GuidanceNote { id: string; text: string; added_at: number }
export interface SprintRow {
  id: string; status: string; goals: string; priority: number;
  steps: number; results: string[];
}
export interface Step { id: string; run: string }
export interface Sprint {
  id: string; status: string; goals: string; priority: number; preemptible: boolean;
  resources_required: Record<string, number>; plan: Step[];
  completed_steps: string[]; detached: Record<string, string>;
  outputs: Record<string, string>; lease: unknown | null;
}
export interface ResultRow { id: string; sprint: string; summary: string }
export interface Ledger {
  capacity: Record<string, number>; used: Record<string, number>;
  available: Record<string, number>; leases: unknown[];
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export interface SprintPatch {
  goals?: string; plan?: Step[]; priority?: number;
  resources_required?: Record<string, number>; preemptible?: boolean;
}

export const api = {
  listPrograms: () => fetch("/api/programs").then(j<ProgramRow[]>),
  getProgram: (id: string) => fetch(`/api/programs/${id}`).then(j<Program>),
  setProgramStatus: (id: string, status: string) =>
    fetch(`/api/programs/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then(j<Program>),
  listGuidance: (id: string) => fetch(`/api/programs/${id}/guidance`).then(j<GuidanceNote[]>),
  addGuidance: (id: string, text: string) =>
    fetch(`/api/programs/${id}/guidance`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<GuidanceNote>),
  removeGuidance: (id: string, noteId: string) =>
    fetch(`/api/programs/${id}/guidance/${noteId}`, { method: "DELETE" }).then(j<void>),
  listSprints: () => fetch("/api/sprints").then(j<SprintRow[]>),
  getSprint: (id: string) => fetch(`/api/sprints/${id}`).then(j<Sprint>),
  submitSprint: (body: { id: string; goals: string; plan: Step[]; program?: string;
                         priority?: number; resources_required?: Record<string, number> }) =>
    fetch("/api/sprints", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Sprint>),
  approveSprint: (id: string) =>
    fetch(`/api/sprints/${id}/approve`, { method: "POST" }).then(j<Sprint>),
  rejectSprint: (id: string) =>
    fetch(`/api/sprints/${id}/reject`, { method: "POST" }).then(j<Sprint>),
  editSprint: (id: string, patch: SprintPatch) =>
    fetch(`/api/sprints/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<Sprint>),
  getResult: (id: string) => fetch(`/api/results/${id}`).then(j<ResultRow>),
  getLedger: () => fetch("/api/ledger").then(j<Ledger>),
};
```

- [ ] **Step 6: Write and pass the `api` client test**

Create `frontend/src/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body),
                 { status, headers: { "Content-Type": "application/json" } }) as Response);
}

describe("api client", () => {
  it("getProgram hits the prefixed path and parses JSON", async () => {
    const f = mockFetch(200, { id: "p1", title: "t", status: "active", goals: "g",
                               report: "r", cycle: 1, sprints: [] });
    const p = await api.getProgram("p1");
    expect(f).toHaveBeenCalledWith("/api/programs/p1");
    expect(p.cycle).toBe(1);
  });

  it("editSprint sends a PATCH with the patch body", async () => {
    const f = mockFetch(200, { id: "sp1" });
    await api.editSprint("sp1", { priority: 5 });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/sprints/sp1");
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ priority: 5 });
  });

  it("throws on a non-ok response", async () => {
    mockFetch(404, { detail: "nope" });
    await expect(api.getSprint("x")).rejects.toThrow("404");
  });
});
```

Run: `cd frontend && npx vitest run`
Expected: PASS (both test files).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api.ts frontend/src/sprintActions.ts frontend/src/api.test.ts frontend/src/sprintActions.test.ts
git commit -m "feat: typed API client + sprint-action guards (tested)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: FastAPI serves the SPA bundle + Docker build stage

**Files:**
- Modify: `src/coscience/http_api.py`, `Dockerfile`
- Test: `tests/test_http_static.py` (new), `tests/test_container_files.py`

**Interfaces:**
- Consumes: `build_app`, `service_from_env`.
- Produces: `create_app()` mounts `frontend/dist` at `/` with an SPA catch-all when the bundle exists; `build_app` stays API-only. `COSCIENCE_UI_DIR` env overrides the bundle path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_http_static.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

import coscience.http_api as http_api


def test_build_app_is_api_only(tmp_path):
    # No SPA mount in build_app: an unknown non-/api path is a plain 404.
    from coscience.service import Service
    client = TestClient(http_api.build_app(Service(tmp_path)))
    assert client.get("/").status_code == 404


def test_create_app_serves_spa_when_bundle_present(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>ui</title>")
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_UI_DIR", str(dist))
    app = http_api.create_app()
    assert isinstance(app, FastAPI)
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert "ui" in client.get("/").text
    # client-side route falls back to index.html
    assert client.get("/programs/p1").status_code == 200
    # API still works under /api
    assert client.get("/api/health").json() == {"status": "ok"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_static.py -q`
Expected: FAIL — `create_app` does not mount the SPA / catch-all.

- [ ] **Step 3: Mount the SPA in `create_app`**

In `src/coscience/http_api.py`, add imports `from pathlib import Path`, `from fastapi.responses import FileResponse`, `from fastapi.staticfiles import StaticFiles`. Replace `create_app` with:

```python
def create_app() -> FastAPI:
    """uvicorn factory: API from the environment, plus the SPA bundle if present."""
    app = build_app(service_from_env())
    ui_dir = Path(os.environ.get("COSCIENCE_UI_DIR",
                                 Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    index = ui_dir / "index.html"
    if index.is_file():
        assets = ui_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            candidate = ui_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)
    return app
```

Note: the catch-all is registered after `build_app` has already added the `/api` router, so `/api/*` routes win; only non-API paths fall through to the SPA.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_static.py tests/test_http_entry.py -q`
Expected: PASS.

- [ ] **Step 5: Add the Docker build stage**

Replace `Dockerfile` with:

```dockerfile
FROM node:20-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[http]"
COPY --from=ui /ui/dist ./frontend/dist

ENV COSCIENCE_REPO=/data \
    COSCIENCE_HOST=0.0.0.0 \
    COSCIENCE_PORT=8000

EXPOSE 8000
CMD ["coscience-http"]
```

- [ ] **Step 6: Update the container test**

In `tests/test_container_files.py`, add to `test_dockerfile_has_required_directives`:

```python
    assert "AS ui" in text                        # node build stage
    assert "npm run build" in text                # builds the SPA
    assert "frontend/dist" in text                # copies the bundle in
```

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_container_files.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/coscience/http_api.py Dockerfile tests/test_http_static.py tests/test_container_files.py
git commit -m "feat: serve SPA bundle from FastAPI + docker build stage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 14: Programs overview view

**Files:**
- Modify: `frontend/src/views/ProgramsOverview.tsx`

**Interfaces:**
- Consumes: `api.listPrograms`, `api.listSprints`.
- Produces: the landing page at `/` — a table of programs with status badge, PM cycle, and sprint counts by status; each row links to `/programs/:id`.

- [ ] **Step 1: Implement the view**

Replace `frontend/src/views/ProgramsOverview.tsx`:

```tsx
import { Badge, Loader, Table, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

const STATUS_COLOR: Record<string, string> = {
  active: "green", paused: "yellow", closed: "gray",
};

export default function ProgramsOverview() {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  const sprints = useQuery({ queryKey: ["sprints"], queryFn: api.listSprints });
  if (programs.isLoading || sprints.isLoading) return <Loader />;
  if (programs.error) return <div>Failed to load programs.</div>;

  const counts = (programId: string) => {
    const rows = (sprints.data ?? []).filter((s) => s.id.startsWith(`${programId}-`));
    const by: Record<string, number> = {};
    for (const s of rows) by[s.status] = (by[s.status] ?? 0) + 1;
    return Object.entries(by).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
  };

  return (
    <>
      <Title order={2} mb="md">Programs</Title>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Title</Table.Th><Table.Th>Status</Table.Th>
            <Table.Th>Sprints</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(programs.data ?? []).map((p) => (
            <Table.Tr key={p.id}>
              <Table.Td><Link to={`/programs/${p.id}`}>{p.title || p.id}</Link></Table.Td>
              <Table.Td>
                <Badge color={STATUS_COLOR[p.status] ?? "blue"}>{p.status}</Badge>
              </Table.Td>
              <Table.Td>{counts(p.id)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </>
  );
}
```

Note: sprint→program association uses the deterministic id convention (`<program>-c<cycle>-<suffix>` and human ids prefixed `<program>-`); the program detail view (Task 15) uses the authoritative `program.sprints` list from `get_program`. This overview's count is a lightweight summary only.

- [ ] **Step 2: Verify the build and tests still pass**

Run: `cd frontend && npm run build && npx vitest run`
Expected: build succeeds; vitest PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/ProgramsOverview.tsx
git commit -m "feat: programs overview view

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 15: Program detail view (report, sprints, guidance, controls)

**Files:**
- Modify: `frontend/src/views/ProgramDetail.tsx`
- Create: `frontend/src/components/ProposeSprintModal.tsx`

**Interfaces:**
- Consumes: `api.getProgram`, `api.listGuidance`, `api.addGuidance`, `api.removeGuidance`, `api.setProgramStatus`, `api.submitSprint`.
- Produces: the `/programs/:id` centerpiece — rendered `report.md`, sprint list (links to sprint detail) grouped by status, a guidance panel (list/add/delete), program controls (pause/resume/close), and a propose-sprint modal.

- [ ] **Step 1: Implement the propose-sprint modal**

Create `frontend/src/components/ProposeSprintModal.tsx`:

```tsx
import { Button, Modal, NumberInput, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { api } from "../api";

interface Props { programId: string; opened: boolean; onClose: () => void; onDone: () => void }

export default function ProposeSprintModal({ programId, opened, onClose, onDone }: Props) {
  const [id, setId] = useState("");
  const [goals, setGoals] = useState("");
  const [run, setRun] = useState("");
  const [priority, setPriority] = useState<number>(0);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    try {
      await api.submitSprint({
        id, goals, program: programId, priority,
        plan: [{ id: "s1", run }],
      });
      onDone(); onClose();
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Propose sprint">
      <Stack>
        <TextInput label="Sprint id" value={id} onChange={(e) => setId(e.currentTarget.value)} />
        <Textarea label="Goals" value={goals} onChange={(e) => setGoals(e.currentTarget.value)} />
        <TextInput label="First step command" value={run}
                   onChange={(e) => setRun(e.currentTarget.value)} />
        <NumberInput label="Priority" value={priority}
                     onChange={(v) => setPriority(Number(v) || 0)} />
        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={submit}>Submit proposal</Button>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 2: Implement the program detail view**

Replace `frontend/src/views/ProgramDetail.tsx`:

```tsx
import {
  ActionIcon, Badge, Button, Card, Group, Loader, Stack, Table, Text,
  TextInput, Title,
} from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";
import ProposeSprintModal from "../components/ProposeSprintModal";

export default function ProgramDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [proposing, setProposing] = useState(false);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const guidance = useQuery({ queryKey: ["guidance", id], queryFn: () => api.listGuidance(id) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["program", id] });
    qc.invalidateQueries({ queryKey: ["guidance", id] });
  };

  if (program.isLoading) return <Loader />;
  if (program.error || !program.data) return <div>Program not found.</div>;
  const p = program.data;

  const setStatus = async (status: string) => { await api.setProgramStatus(id, status); refresh(); };
  const addNote = async () => { if (note.trim()) { await api.addGuidance(id, note.trim()); setNote(""); refresh(); } };
  const delNote = async (nid: string) => { await api.removeGuidance(id, nid); refresh(); };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{p.title || p.id} <Badge ml="sm">{p.status}</Badge></Title>
        <Group>
          <Text size="sm" c="dimmed">PM cycle {p.cycle}</Text>
          {p.status !== "active" && <Button variant="light" onClick={() => setStatus("active")}>Resume</Button>}
          {p.status === "active" && <Button variant="light" color="yellow" onClick={() => setStatus("paused")}>Pause</Button>}
          {p.status !== "closed" && <Button variant="light" color="gray" onClick={() => setStatus("closed")}>Close</Button>}
          <Button onClick={() => setProposing(true)}>Propose sprint</Button>
        </Group>
      </Group>

      <Card withBorder>
        <Title order={4} mb="xs">PM report</Title>
        <Markdown>{p.report || "_No report yet._"}</Markdown>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">Human guidance</Title>
        <Stack gap="xs">
          {(guidance.data ?? []).map((g) => (
            <Group key={g.id} justify="space-between">
              <Text>{g.text}</Text>
              <ActionIcon variant="subtle" color="red" onClick={() => delNote(g.id)}>✕</ActionIcon>
            </Group>
          ))}
          <Group>
            <TextInput style={{ flex: 1 }} placeholder="Add a steer for the PM…"
                       value={note} onChange={(e) => setNote(e.currentTarget.value)} />
            <Button onClick={addNote}>Add</Button>
          </Group>
        </Stack>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">Sprints</Title>
        <Table striped withTableBorder>
          <Table.Thead><Table.Tr>
            <Table.Th>Id</Table.Th><Table.Th>Status</Table.Th><Table.Th>Goals</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {p.sprints.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td><Link to={`/sprints/${s.id}`}>{s.id}</Link></Table.Td>
                <Table.Td><Badge>{s.status}</Badge></Table.Td>
                <Table.Td>{s.goals}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <ProposeSprintModal programId={id} opened={proposing}
                          onClose={() => setProposing(false)} onDone={refresh} />
    </Stack>
  );
}
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/ProgramDetail.tsx frontend/src/components/ProposeSprintModal.tsx
git commit -m "feat: program detail view (report, sprints, guidance, controls)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 16: Sprint detail view + edit modal

**Files:**
- Modify: `frontend/src/views/SprintDetail.tsx`
- Create: `frontend/src/components/SprintEditModal.tsx`

**Interfaces:**
- Consumes: `api.getSprint`, `api.approveSprint`, `api.rejectSprint`, `api.editSprint`, `availableActions`, `editableFields`.
- Produces: `/sprints/:id` — full sprint detail, a status-driven action bar (Approve/Reject/Edit), and an edit modal whose fields are disabled per `editableFields`.

- [ ] **Step 1: Implement the edit modal**

Create `frontend/src/components/SprintEditModal.tsx`:

```tsx
import { Button, Modal, NumberInput, Stack, Switch, Textarea } from "@mantine/core";
import { useState } from "react";
import { api, type Sprint, type SprintPatch } from "../api";
import { editableFields, type SprintStatus } from "../sprintActions";

interface Props { sprint: Sprint; opened: boolean; onClose: () => void; onDone: () => void }

export default function SprintEditModal({ sprint, opened, onClose, onDone }: Props) {
  const f = editableFields(sprint.status as SprintStatus);
  const [goals, setGoals] = useState(sprint.goals);
  const [priority, setPriority] = useState<number>(sprint.priority);
  const [preemptible, setPreemptible] = useState<boolean>(sprint.preemptible);
  const [error, setError] = useState("");

  const save = async () => {
    setError("");
    const patch: SprintPatch = {};
    if (f.goals && goals !== sprint.goals) patch.goals = goals;
    if (f.priority && priority !== sprint.priority) patch.priority = priority;
    if (f.preemptible && preemptible !== sprint.preemptible) patch.preemptible = preemptible;
    try { await api.editSprint(sprint.id, patch); onDone(); onClose(); }
    catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Edit ${sprint.id}`}>
      <Stack>
        <Textarea label="Goals" value={goals} disabled={!f.goals}
                  onChange={(e) => setGoals(e.currentTarget.value)} />
        <NumberInput label="Priority" value={priority} disabled={!f.priority}
                     onChange={(v) => setPriority(Number(v) || 0)} />
        <Switch label="Preemptible" checked={preemptible} disabled={!f.preemptible}
                onChange={(e) => setPreemptible(e.currentTarget.checked)} />
        {!f.goals && <span style={{ fontSize: 12, color: "gray" }}>
          Goals/plan are editable only while proposed. Priority/resources affect future
          scheduling only, not a lease already held.</span>}
        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={save}>Save</Button>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 2: Implement the sprint detail view**

Replace `frontend/src/views/SprintDetail.tsx`:

```tsx
import { Badge, Button, Card, Code, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { availableActions, type SprintStatus } from "../sprintActions";
import SprintEditModal from "../components/SprintEditModal";

export default function SprintDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const sprint = useQuery({ queryKey: ["sprint", id], queryFn: () => api.getSprint(id) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["sprint", id] });

  if (sprint.isLoading) return <Loader />;
  if (sprint.error || !sprint.data) return <div>Sprint not found.</div>;
  const s = sprint.data;
  const actions = availableActions(s.status as SprintStatus);

  const approve = async () => { await api.approveSprint(id); refresh(); };
  const reject = async () => { await api.rejectSprint(id); refresh(); };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{s.id} <Badge ml="sm">{s.status}</Badge></Title>
        <Group>
          {actions.includes("approve") && <Button color="green" onClick={approve}>Approve</Button>}
          {actions.includes("reject") && <Button color="red" variant="light" onClick={reject}>Reject</Button>}
          {actions.includes("edit") && <Button variant="light" onClick={() => setEditing(true)}>Edit</Button>}
        </Group>
      </Group>

      <Card withBorder>
        <Text><b>Goals:</b> {s.goals}</Text>
        <Text><b>Priority:</b> {s.priority} &nbsp; <b>Preemptible:</b> {String(s.preemptible)}</Text>
        <Text><b>Resources:</b> {JSON.stringify(s.resources_required)}</Text>
        <Text><b>Lease:</b> {s.lease ? "held" : "none"}</Text>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">Plan</Title>
        <Table withTableBorder>
          <Table.Thead><Table.Tr><Table.Th>Step</Table.Th><Table.Th>Command</Table.Th><Table.Th>Done</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {s.plan.map((step) => (
              <Table.Tr key={step.id}>
                <Table.Td>{step.id}</Table.Td>
                <Table.Td><Code>{step.run}</Code></Table.Td>
                <Table.Td>{s.completed_steps.includes(step.id) ? "✓" : ""}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <SprintEditModal sprint={s} opened={editing}
                       onClose={() => setEditing(false)} onDone={refresh} />
    </Stack>
  );
}
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/SprintDetail.tsx frontend/src/components/SprintEditModal.tsx
git commit -m "feat: sprint detail view + edit modal

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 17: Result + Ledger views

**Files:**
- Modify: `frontend/src/views/ResultDetail.tsx`, `frontend/src/views/Ledger.tsx`

**Interfaces:**
- Consumes: `api.getResult`, `api.getLedger`.
- Produces: `/results/:id` (rendered summary + link to its sprint) and `/ledger` (capacity/used/available + active leases table).

- [ ] **Step 1: Implement the result view**

Replace `frontend/src/views/ResultDetail.tsx`:

```tsx
import { Card, Loader, Stack, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";

export default function ResultDetail() {
  const { id = "" } = useParams();
  const result = useQuery({ queryKey: ["result", id], queryFn: () => api.getResult(id) });
  if (result.isLoading) return <Loader />;
  if (result.error || !result.data) return <div>Result not found.</div>;
  const r = result.data;
  return (
    <Stack>
      <Title order={2}>Result {r.id}</Title>
      <div>Sprint: <Link to={`/sprints/${r.sprint}`}>{r.sprint}</Link></div>
      <Card withBorder><Markdown>{r.summary}</Markdown></Card>
    </Stack>
  );
}
```

- [ ] **Step 2: Implement the ledger view**

Replace `frontend/src/views/Ledger.tsx`:

```tsx
import { Card, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function Ledger() {
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });
  if (ledger.isLoading) return <Loader />;
  if (ledger.error || !ledger.data) return <div>Failed to load ledger.</div>;
  const l = ledger.data;
  const keys = Object.keys(l.capacity);
  return (
    <Stack>
      <Title order={2}>Resources</Title>
      <Group>
        {keys.map((k) => (
          <Card withBorder key={k}>
            <Text fw={700}>{k}</Text>
            <Text size="sm">capacity {l.capacity[k]}</Text>
            <Text size="sm">used {l.used[k] ?? 0}</Text>
            <Text size="sm">available {l.available[k] ?? 0}</Text>
          </Card>
        ))}
      </Group>
      <Card withBorder>
        <Title order={4} mb="xs">Active leases</Title>
        <Table withTableBorder>
          <Table.Thead><Table.Tr><Table.Th>Lease</Table.Th><Table.Th>Sprint</Table.Th><Table.Th>Amounts</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {l.leases.map((lease, i) => {
              const x = lease as { id: string; sprint_id: string; amounts: unknown };
              return (
                <Table.Tr key={i}>
                  <Table.Td>{x.id}</Table.Td>
                  <Table.Td>{x.sprint_id}</Table.Td>
                  <Table.Td>{JSON.stringify(x.amounts)}</Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
```

- [ ] **Step 3: Verify the build and the full frontend test run**

Run: `cd frontend && npm run build && npx vitest run`
Expected: build succeeds; vitest PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/ResultDetail.tsx frontend/src/views/Ledger.tsx
git commit -m "feat: result + ledger views

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 18: Acceptance runbook

**Files:**
- Create: `docs/superpowers/plans/2026-06-25-dashboard-acceptance.md`

**Interfaces:**
- Consumes: the whole stack (CLI `coscience pm`, `coscience dispatch`, `coscience-http`).
- Produces: a manual end-to-end proof that a human can drive the loop from the browser.

- [ ] **Step 1: Write the runbook**

Create `docs/superpowers/plans/2026-06-25-dashboard-acceptance.md`:

```markdown
# Dashboard acceptance runbook

Prereqs: `/home/oleg/venvs/coscience` with `.[http]` installed; Node 20+; an authed `claude` CLI for the PM step.

1. **Build the SPA:** `cd frontend && npm install && npm run build`.
2. **Seed a program:** pick a scratch repo dir `$REPO`; run
   `coscience program create --repo $REPO --id demo --title "Demo" --goals "Find X"`.
3. **Serve:** `COSCIENCE_REPO=$REPO coscience-http` (serves API at /api and the SPA at /).
   Open `http://localhost:8000/`.
4. **See the program:** the Programs table shows `demo` (active). Open it; the PM report is empty.
5. **Add a guidance note:** in the program's guidance panel add "prefer cheap in-vitro assays". Confirm it appears.
6. **Run one real PM cycle:** `COSCIENCE_REPO=$REPO coscience pm --repo $REPO --once`.
   Reload the program: the PM report is populated and proposed sprint(s) appear, reflecting the guidance.
7. **Approve from the UI:** open a proposed sprint; click **Approve**; status flips to approved.
8. **Reject from the UI:** propose or pick another proposed sprint; click **Reject**; status flips to canceled.
9. **Run it:** `COSCIENCE_REPO=$REPO coscience dispatch --repo $REPO --once`; a result appears; the sprint reaches done; the result is viewable.
10. **Pause:** click **Pause** on the program; run `coscience pm --repo $REPO --once` again and confirm no new proposals (the PM skips paused programs).

If every step behaves as described, the dashboard closes the human-in-the-loop.
```

- [ ] **Step 2: Run the full backend suite once more**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-06-25-dashboard-acceptance.md
git commit -m "docs: dashboard acceptance runbook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final review

After Task 18, run the final whole-branch review (superpowers:requesting-code-review on the most capable model) over the full diff from the branch base, then `superpowers:finishing-a-development-branch`.
