"""HTTP (REST) API exposing coscience.service.Service via FastAPI.

Thin wrapper: each route calls one Service method and returns its (already
JSON-serialisable) result. Service errors map to HTTP status codes. This module
must not import mcp / coscience.mcp_server — the transports are independent
siblings over Service.
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coscience.service import NotFoundError, Service, service_from_env


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


class SprintPatch(BaseModel):
    goals: str | None = None
    plan: list[StepIn] | None = None
    priority: int | None = None
    resources_required: dict[str, float] | None = None
    preemptible: bool | None = None


class ProgramStatusIn(BaseModel):
    status: str


class GuidanceIn(BaseModel):
    text: str


def build_app(service: Service, title: str = "Co-Science Platform") -> FastAPI:
    app = FastAPI(title=title, version="0.0.0")
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @api.get("/sprints")
    def list_sprints(status: str | None = Query(default=None)) -> list[dict]:
        try:
            return service.list_sprints(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/sprints", status_code=201)
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

    @api.get("/sprints/{sprint_id}")
    def get_sprint(sprint_id: str) -> dict:
        try:
            return service.get_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")

    @api.post("/sprints/{sprint_id}/approve")
    def approve_sprint(sprint_id: str) -> dict:
        try:
            service.approve_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        return service.get_sprint(sprint_id)

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

    @api.get("/results")
    def list_results() -> list[dict]:
        return service.list_results()

    @api.get("/results/{result_id}")
    def get_result(result_id: str) -> dict:
        try:
            return service.get_result(result_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"result not found: {result_id}")

    @api.get("/ledger")
    def ledger_status() -> dict:
        return service.ledger_status()

    @api.get("/programs")
    def list_programs(status: str | None = Query(default=None)) -> list[dict]:
        try:
            return service.list_programs(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.get("/programs/{program_id}")
    def get_program(program_id: str) -> dict:
        try:
            return service.get_program(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/status")
    def set_program_status(program_id: str, body: ProgramStatusIn) -> dict:
        try:
            service.set_program_status(program_id, body.status)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_program(program_id)

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

    app.include_router(api)
    return app


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


def main() -> None:
    """Console entry point: run the HTTP API under uvicorn."""
    host = os.environ.get("COSCIENCE_HOST", "0.0.0.0")
    port = int(os.environ.get("COSCIENCE_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
