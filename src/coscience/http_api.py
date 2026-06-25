"""HTTP (REST) API exposing coscience.service.Service via FastAPI.

Thin wrapper: each route calls one Service method and returns its (already
JSON-serialisable) result. Service errors map to HTTP status codes. This module
must not import mcp / coscience.mcp_server — the transports are independent
siblings over Service.
"""
from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, HTTPException, Query
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


def create_app() -> FastAPI:
    """uvicorn factory: build the app from the environment (COSCIENCE_REPO)."""
    return build_app(service_from_env())


def main() -> None:
    """Console entry point: run the HTTP API under uvicorn."""
    host = os.environ.get("COSCIENCE_HOST", "0.0.0.0")
    port = int(os.environ.get("COSCIENCE_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
