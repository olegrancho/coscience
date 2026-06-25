# Co-Science Platform — Phase 1b-2b-iii (HTTP API + Container) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the transport-agnostic `coscience.service.Service` as a **REST HTTP API** (FastAPI) and ship it as a **containerized** coordination service (Dockerfile + docker compose). This is the small always-on service from the design's "Approach C": a human or the future PM agent can submit/approve/inspect sprints and read results/ledger over HTTP, and the whole thing runs in one container against a mounted OKF repo volume.

**Architecture:** A `build_app(service: Service) -> FastAPI` factory (mirrors the MCP `build_server(service)` seam) registering eight routes that each call exactly one `Service` method. Request bodies are validated by Pydantic models; `Service` errors map to HTTP status codes via `HTTPException` (`NotFoundError` → 404, duplicate-id `ValueError` → 409, bad status filter → 422; empty `plan` is rejected at the schema layer → 422). A `create_app()` factory resolves a `Service` from the environment for uvicorn; `main()` runs uvicorn. The container installs the package's `[http]` extra and runs the `coscience-http` console script against a `/data` repo volume.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest, **`fastapi` 0.138.0 + `uvicorn` 0.49.0** (already installed in the venv), `httpx` (already installed; FastAPI's `TestClient` uses it). Docker + docker compose for the container (runtime, not exercised by the unit suite).

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** add an **optional** extra `[http] = ["fastapi>=0.110", "uvicorn>=0.29"]` in `pyproject.toml`; add `httpx>=0.27` to the existing `dev` extra (TestClient needs it). Core runtime stays `pyyaml`-only; both `mcp` and `http` extras are opt-in. All are already installed in the venv — do not pip/uv install anything.
- **`http_api.py` must NOT import `mcp` or `coscience.mcp_server`** (importing mcp_server pulls the optional `mcp` dep). The two transport modules are siblings over `Service`, independent of each other.
- **THIN wrapper, no business logic:** each route calls exactly one `Service` method and returns its result; the only transformation is Pydantic request-body validation and error→status mapping. No scheduling, no substrate/ledger access, no data reshaping.
- **Eight routes, mapping to Service:** `GET /health`; `GET /sprints`(list, `?status=`); `POST /sprints`(submit); `GET /sprints/{id}`; `POST /sprints/{id}/approve`; `GET /results`; `GET /results/{id}`; `GET /ledger`.
- **Error mapping:** `NotFoundError` → 404; duplicate-id `ValueError` from submit → 409; invalid `?status=` value → 422; empty `plan` → 422 (Pydantic `min_length=1`). A raw exception/500 must never be the documented path for these cases.
- **Returns stay JSON-serialisable plain data** — FastAPI serialises the `Service` dict/list verbatim.
- **Backward compatibility:** all existing tests stay green; **no edits to Phase 0/1/1b/mcp files**. The one prior-file touch is additive: a `service_from_env()` helper added to `service.py` (Task 2), used by the HTTP entry point.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only 1b-2b-iii is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 / 1 / 1b-1 / 1b-2a / 1b-2b-i / 1b-2b-ii | skeleton; scheduling; job kill; restart reconciliation; service core; MCP server | DONE |
| **1b-2b-iii — HTTP API + container** (this plan) | wrap `Service` as REST (deps: fastapi/uvicorn) + docker compose | **planned here** |
| 1b-2c — PID-reuse guard | identity token before signalling a stored PID | next |
| Phase 2 | PM agent + internal dashboard | after |

---

## File Structure

```
src/coscience/
  service.py        # MODIFY (Task 2): add module-level service_from_env() helper (env COSCIENCE_REPO -> cwd)
  http_api.py       # NEW (Task 1): build_app(service) -> FastAPI, 8 routes, Pydantic models, error mapping
                    #     (Task 2): create_app() factory + main() (uvicorn)
tests/
  test_http_api.py   # NEW (Task 1): TestClient over a tmp substrate, all routes + error codes
  test_http_entry.py # NEW (Task 2): create_app builds an app; main() invokes uvicorn.run (monkeypatched)
pyproject.toml       # MODIFY (Task 2): [http] extra, httpx in dev, coscience-http console script
Dockerfile           # NEW (Task 3)
docker-compose.yml   # NEW (Task 3)
tests/test_container_files.py  # NEW (Task 3): structural validation (no docker build needed)
docs/superpowers/plans/phase1b2biii-http-acceptance.md  # NEW (Task 3): manual docker/curl smoke steps
```

---

## Task 1: HTTP API — `build_app` + eight routes

**Files:**
- Create: `src/coscience/http_api.py`
- Test: `tests/test_http_api.py`

**Interfaces:**
- Pydantic models:
  - `class StepIn(BaseModel)`: `id: str`, `run: str`.
  - `class SprintSubmit(BaseModel)`: `id: str`, `goals: str`, `plan: list[StepIn] = Field(min_length=1)`, `program: str | None = None`, `priority: int = 0`, `preemptible: bool = True`, `resources_required: dict[str, float] | None = None`.
- `build_app(service: Service) -> FastAPI` registering the eight routes. Each route is thin; errors map to `HTTPException`. The list route also maps an invalid `?status=` (Service raises `ValueError` from `SprintStatus(status)`) to 422.

- [ ] **Step 1: Write the failing tests**

`tests/test_http_api.py`:
```python
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Result
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    client = TestClient(build_app(svc))
    client.svc = svc  # expose for seeding results
    return client


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_submit_then_get_and_list(client):
    body = {"id": "sp1", "goals": "cure",
            "plan": [{"id": "s1", "run": "echo hi"}],
            "priority": 3, "resources_required": {"gpu": 1}}
    r = client.post("/sprints", json=body)
    assert r.status_code == 201
    created = r.json()
    assert created["id"] == "sp1"
    assert created["status"] == "proposed"
    assert created["plan"] == [{"id": "s1", "run": "echo hi"}]

    r = client.get("/sprints", params={"status": "proposed"})
    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == ["sp1"]

    r = client.get("/sprints/sp1")
    assert r.status_code == 200
    assert r.json()["priority"] == 3
    assert r.json()["lease"] is None


def test_approve_changes_status(client):
    client.post("/sprints", json={"id": "sp1", "goals": "g",
                                  "plan": [{"id": "s1", "run": "true"}]})
    r = client.post("/sprints/sp1/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert client.get("/sprints", params={"status": "proposed"}).json() == []


def test_results_round_trip(client):
    client.svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    assert client.get("/results").json() == [{"id": "r1", "sprint": "sp1", "summary": "found X"}]
    assert client.get("/results/r1").json()["summary"] == "found X"


def test_ledger_status_shape(client):
    body = client.get("/ledger").json()
    assert set(body) == {"capacity", "used", "available", "leases"}


def test_missing_sprint_is_404(client):
    assert client.get("/sprints/nope").status_code == 404


def test_approve_missing_is_404(client):
    assert client.post("/sprints/nope/approve").status_code == 404


def test_missing_result_is_404(client):
    assert client.get("/results/nope").status_code == 404


def test_duplicate_submit_is_409(client):
    body = {"id": "sp1", "goals": "g", "plan": [{"id": "s1", "run": "true"}]}
    assert client.post("/sprints", json=body).status_code == 201
    assert client.post("/sprints", json=body).status_code == 409


def test_empty_plan_is_422(client):
    assert client.post("/sprints", json={"id": "sp1", "goals": "g", "plan": []}).status_code == 422


def test_invalid_status_filter_is_422(client):
    assert client.get("/sprints", params={"status": "bogus"}).status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.http_api'`.

- [ ] **Step 3: Implement `src/coscience/http_api.py`**

```python
"""HTTP (REST) API exposing coscience.service.Service via FastAPI.

Thin wrapper: each route calls one Service method and returns its (already
JSON-serialisable) result. Service errors map to HTTP status codes. This module
must not import mcp / coscience.mcp_server — the transports are independent
siblings over Service.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from coscience.service import NotFoundError, Service


class StepIn(BaseModel):
    id: str
    run: str


class SprintSubmit(BaseModel):
    id: str
    goals: str
    plan: list[StepIn] = Field(min_length=1)
    program: str | None = None
    priority: int = 0
    preemptible: bool = True
    resources_required: dict[str, float] | None = None


def build_app(service: Service, title: str = "Co-Science Platform") -> FastAPI:
    app = FastAPI(title=title, version="0.0.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/sprints")
    def list_sprints(status: str | None = Query(default=None)) -> list[dict]:
        try:
            return service.list_sprints(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/sprints", status_code=201)
    def submit_sprint(body: SprintSubmit) -> dict:
        try:
            service.submit_sprint(
                id=body.id, goals=body.goals,
                plan=[step.model_dump() for step in body.plan],
                program=body.program, priority=body.priority,
                preemptible=body.preemptible,
                resources_required=body.resources_required,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return service.get_sprint(body.id)

    @app.get("/sprints/{sprint_id}")
    def get_sprint(sprint_id: str) -> dict:
        try:
            return service.get_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")

    @app.post("/sprints/{sprint_id}/approve")
    def approve_sprint(sprint_id: str) -> dict:
        try:
            service.approve_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        return service.get_sprint(sprint_id)

    @app.get("/results")
    def list_results() -> list[dict]:
        return service.list_results()

    @app.get("/results/{result_id}")
    def get_result(result_id: str) -> dict:
        try:
            return service.get_result(result_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"result not found: {result_id}")

    @app.get("/ledger")
    def ledger_status() -> dict:
        return service.ledger_status()

    return app
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_api.py -v`
Expected: all pass. (A `StarletteDeprecationWarning` about httpx may print; it does not fail the suite.)

- [ ] **Step 5: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_api.py
git commit -m "feat: HTTP REST API wrapping Service (FastAPI, eight routes)"
```

---

## Task 2: Entry point + packaging

**Files:**
- Modify: `src/coscience/service.py` (add `service_from_env`), `src/coscience/http_api.py` (add `create_app` + `main`)
- Modify: `pyproject.toml`
- Test: `tests/test_http_entry.py`

**Interfaces:**
- In `service.py`, add a module-level function:
  - `service_from_env() -> Service` — `repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))`; returns `Service(repo_root)`. (Lives in core `service.py` so any transport can reuse it without importing another transport. `mcp_server.py` keeps its own existing private `_service_from_env` for now — unifying it is a deferred cleanup, out of scope here.)
- In `http_api.py`, add:
  - `create_app() -> FastAPI` — `return build_app(service_from_env())`. Suitable for `uvicorn coscience.http_api:create_app --factory`.
  - `main() -> None` — read `COSCIENCE_HOST` (default `"0.0.0.0"`) and `COSCIENCE_PORT` (default `"8000"`, `int()`-cast); call `uvicorn.run(create_app(), host=host, port=port)`. Import `uvicorn` *inside* `main`/`create_app` scope is unnecessary — a top-level `import uvicorn` is fine (uvicorn is part of the `[http]` extra alongside fastapi).
- `pyproject.toml`:
  - `dev = ["pytest>=8", "httpx>=0.27"]`
  - add `http = ["fastapi>=0.110", "uvicorn>=0.29"]`
  - `[project.scripts]` gains `coscience-http = "coscience.http_api:main"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_http_entry.py`:
```python
from fastapi import FastAPI

import coscience.http_api as http_api
from coscience.service import Service, service_from_env


def test_service_from_env_uses_repo_var(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    svc = service_from_env()
    assert isinstance(svc, Service)
    assert svc.repo_root == tmp_path


def test_service_from_env_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_REPO", raising=False)
    monkeypatch.chdir(tmp_path)
    assert service_from_env().repo_root == tmp_path


def test_create_app_builds_fastapi(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    assert isinstance(http_api.create_app(), FastAPI)


def test_main_runs_uvicorn(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_PORT", "9999")
    captured = {}

    def fake_run(app, host, port):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(http_api.uvicorn, "run", fake_run)
    http_api.main()
    assert captured["port"] == 9999
    assert captured["host"] == "0.0.0.0"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_entry.py -v`
Expected: FAIL with `ImportError: cannot import name 'service_from_env'` (and `AttributeError` on `create_app`/`main`).

- [ ] **Step 3: Implement**

In `src/coscience/service.py`, add at module level (top imports gain `import os`):
```python
def service_from_env() -> "Service":
    """Construct a Service from COSCIENCE_REPO (default: current directory)."""
    repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))
    return Service(repo_root)
```

In `src/coscience/http_api.py`, add a top-level `import uvicorn`, update the import to `from coscience.service import NotFoundError, Service, service_from_env`, and append:
```python
def create_app() -> FastAPI:
    """uvicorn factory: build the app from the environment (COSCIENCE_REPO)."""
    return build_app(service_from_env())


def main() -> None:
    """Console entry point: run the HTTP API under uvicorn."""
    host = os.environ.get("COSCIENCE_HOST", "0.0.0.0")
    port = int(os.environ.get("COSCIENCE_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
```
(add `import os` to `http_api.py`).

Update `pyproject.toml`:
```toml
[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]
mcp = ["mcp>=1.2"]
http = ["fastapi>=0.110", "uvicorn>=0.29"]

[project.scripts]
coscience = "coscience.cli:main"
coscience-mcp = "coscience.mcp_server:main"
coscience-http = "coscience.http_api:main"
```

- [ ] **Step 4: Run the entry tests, then the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_entry.py -v`
Expected: 4 passed.

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass (prior 113 + Task 1/2 additions). Record the count.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py src/coscience/http_api.py pyproject.toml tests/test_http_entry.py
git commit -m "feat: coscience-http entry point + [http] extra + service_from_env"
```

---

## Task 3: Containerization (Dockerfile + compose) + manual acceptance

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`
- Test: `tests/test_container_files.py`
- Create: `docs/superpowers/plans/phase1b2biii-http-acceptance.md`

**Interfaces / required content:**
- `Dockerfile`: base `python:3.12-slim`; `WORKDIR /app`; copy `pyproject.toml` and `src/`; `pip install --no-cache-dir ".[http]"`; set env `COSCIENCE_REPO=/data`, `COSCIENCE_HOST=0.0.0.0`, `COSCIENCE_PORT=8000`; `EXPOSE 8000`; `CMD ["coscience-http"]`.
- `docker-compose.yml`: one service `coscience`, `build: .`, port `"8000:8000"`, volume `./data:/data`, environment `COSCIENCE_REPO: /data`.
- The unit test validates **file structure only** (no `docker build`/`docker compose up` in the suite — those are the manual-acceptance step). It parses the compose YAML with PyYAML and asserts the Dockerfile contains the required directives.

- [ ] **Step 1: Write the failing tests**

`tests/test_container_files.py`:
```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_has_required_directives():
    text = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in text
    assert ".[http]" in text                      # installs the http extra
    assert "EXPOSE 8000" in text
    assert "coscience-http" in text               # runs the console script
    assert "COSCIENCE_REPO=/data" in text


def test_compose_service_shape():
    spec = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    svc = spec["services"]["coscience"]
    assert svc["build"] == "."
    assert "8000:8000" in svc["ports"]
    assert any(v.endswith(":/data") for v in svc["volumes"])
    assert svc["environment"]["COSCIENCE_REPO"] == "/data"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_container_files.py -v`
Expected: FAIL with `FileNotFoundError` (Dockerfile / docker-compose.yml absent).

- [ ] **Step 3: Create the container files**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[http]"

ENV COSCIENCE_REPO=/data \
    COSCIENCE_HOST=0.0.0.0 \
    COSCIENCE_PORT=8000

EXPOSE 8000
CMD ["coscience-http"]
```

`docker-compose.yml`:
```yaml
services:
  coscience:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      COSCIENCE_REPO: /data
```

- [ ] **Step 4: Run the tests to verify they pass, then the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_container_files.py -v`
Expected: 2 passed.

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass. Record the count.

- [ ] **Step 5: Write the manual-acceptance doc**

Create `docs/superpowers/plans/phase1b2biii-http-acceptance.md` — a short runbook the human runs by hand (Docker is not exercised by the unit suite):
```markdown
# Phase 1b-2b-iii — HTTP API / container manual acceptance

Prereq: Docker + docker compose installed; run from the repo root.

1. Build & start:  `docker compose up --build -d`
2. Health:         `curl -s localhost:8000/health`           -> {"status":"ok"}
3. Submit a sprint:
   curl -s -X POST localhost:8000/sprints -H 'content-type: application/json' \
     -d '{"id":"sp1","goals":"smoke","plan":[{"id":"s1","run":"echo hi"}]}'
   -> 201, returns the created sprint detail (status "proposed")
4. List proposed:  `curl -s 'localhost:8000/sprints?status=proposed'`  -> [{"id":"sp1",...}]
5. Approve:        `curl -s -X POST localhost:8000/sprints/sp1/approve` -> status "approved"
6. Ledger:         `curl -s localhost:8000/ledger`  -> {"capacity":...,"used":...,"available":...,"leases":[...]}
7. 404 check:      `curl -s -o /dev/null -w '%{http_code}' localhost:8000/sprints/nope`  -> 404
8. Persistence:    the sprint is on disk under ./data (mounted at /data). `ls ./data/sprints/sp1`.
9. Interactive docs: open http://localhost:8000/docs (FastAPI Swagger UI).
10. Tear down:     `docker compose down`
```

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml tests/test_container_files.py docs/superpowers/plans/phase1b2biii-http-acceptance.md
git commit -m "feat: containerize HTTP API (Dockerfile, compose) + acceptance runbook"
```

---

## Self-Review

**Spec coverage:**
- eight REST routes 1:1 over Service, Pydantic validation, error→status mapping → Task 1 ✓
- uvicorn entry point + env resolution + packaging extra/console script → Task 2 ✓
- container (Dockerfile + compose) + structural test + manual acceptance runbook → Task 3 ✓
- thin wrapper, no business logic, JSON passthrough, http module independent of mcp → Global Constraints ✓
- Explicitly deferred: PID-reuse guard (1b-2c); auth/TLS (the service is meant to sit behind the oversight committee's own boundary for now); unifying `mcp_server._service_from_env` onto the new `service_from_env`.

**Placeholder scan:** complete code/content in every step; real assertions; no TBD. ✓

**Type consistency:** `build_app(service: Service) -> FastAPI`; `create_app() -> FastAPI`; `main()`; `service_from_env() -> Service` in service.py; routes return the same plain dict/list `Service` returns; `NotFoundError`→404, dup `ValueError`→409, bad status→422, empty plan→422. ✓

**Known simplifications (intentional):**
- No auth/TLS in this increment (local/trusted-network service; the design puts the human oversight boundary outside it).
- The container runs a single uvicorn worker against a mounted repo volume (single-writer model preserved; the dispatcher remains the only scheduler).
- Container correctness is validated structurally in-suite + a manual `docker compose up` runbook, not by building the image in CI (keeps the suite hermetic and fast).
