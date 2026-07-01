"""HTTP (REST) API exposing coscience.service.Service via FastAPI.

Thin wrapper: each route calls one Service method and returns its (already
JSON-serialisable) result. Service errors map to HTTP status codes. This module
must not import mcp / coscience.mcp_server — the transports are independent
siblings over Service.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coscience.service import NotFoundError, Service, service_from_env

_REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def server_version() -> str:
    """Short git SHA of the code this server is running, resolved once at startup.

    Lets the dashboard detect when a long-lived server has drifted from the
    bundle it's serving (e.g. code was rebuilt but the process never bounced).
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


class SprintSubmit(BaseModel):
    id: str
    goals: str
    plan: list[str] = Field(min_length=1)   # suggested steps in plain language
    program: str | None = None
    priority: int = 0
    preemptible: bool = True
    resources_required: dict[str, float] | None = None


class SprintPatch(BaseModel):
    goals: str | None = None
    plan: list[str] | None = None
    priority: int | None = None
    resources_required: dict[str, float] | None = None
    preemptible: bool | None = None
    model: str | None = None


class VoteIn(BaseModel):
    by: str                       # opaque per-browser voter id
    value: int                    # +1 👍, -1 👎, 0 clear (toggling handled server-side)


class ProgramStatusIn(BaseModel):
    status: str


class ProgramModelIn(BaseModel):
    model: str = ""


class ProgramWorkdirIn(BaseModel):
    workdir: str = ""


class GuidanceIn(BaseModel):
    text: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1)


class IdeaIn(BaseModel):
    text: str = Field(min_length=1)


class IdeaPinIn(BaseModel):
    pinned: bool


class IdeaDemoteIn(BaseModel):
    demoted: bool = True


class SprintCommentIn(BaseModel):
    text: str = Field(min_length=1)
    target: str = "worker"          # 'worker' (steers the agent) or 'pm' (steers the planner)


def build_app(service: Service, title: str = "Co-Science Platform") -> FastAPI:
    app = FastAPI(title=title, version="0.0.0")
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @api.get("/version")
    def version() -> dict:
        return {"sha": server_version()}

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
                plan=list(body.plan),
                program=body.program, priority=body.priority,
                preemptible=body.preemptible,
                resources_required=body.resources_required,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return service.get_sprint(body.id)

    @api.get("/sprints/{sprint_id}")
    def get_sprint(sprint_id: str, viewer: str = "") -> dict:
        try:
            return service.get_sprint(sprint_id, viewer=viewer)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")

    @api.get("/sprints/{sprint_id}/files")
    def sprint_files(sprint_id: str) -> list[dict]:
        try:
            return service.list_sprint_files(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")

    @api.get("/sprints/{sprint_id}/files/{name}")
    def sprint_file(sprint_id: str, name: str) -> dict:
        try:
            return service.read_sprint_file(sprint_id, name)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"file not found: {name}")

    @api.post("/sprints/{sprint_id}/comments", status_code=201)
    def comment_sprint(sprint_id: str, body: SprintCommentIn) -> dict:
        try:
            return service.add_sprint_comment(sprint_id, body.text, target=body.target)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/sprints/{sprint_id}/approve")
    def approve_sprint(sprint_id: str) -> dict:
        try:
            service.approve_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/run")
    def run_sprint(sprint_id: str) -> dict:
        try:
            service.run_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/send_back")
    def send_back_sprint(sprint_id: str) -> dict:
        try:
            service.send_back_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/vote")
    def vote_sprint(sprint_id: str, body: VoteIn) -> dict:
        try:
            return service.vote_sprint(sprint_id, body.by, body.value)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/sprints/{sprint_id}/reject")
    def reject_sprint(sprint_id: str) -> dict:
        try:
            service.reject_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/demote")
    def demote_sprint(sprint_id: str) -> dict:
        try:
            return service.demote_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.patch("/sprints/{sprint_id}")
    def patch_sprint(sprint_id: str, body: SprintPatch) -> dict:
        fields = body.model_dump(exclude_unset=True)
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

    @api.get("/usage")
    def usage_stats() -> dict:
        return service.usage_stats()

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

    @api.post("/programs/{program_id}/model")
    def set_program_model(program_id: str, body: ProgramModelIn) -> dict:
        try:
            return service.set_program_model(program_id, body.model)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/workdir")
    def set_program_workdir(program_id: str, body: ProgramWorkdirIn) -> dict:
        try:
            return service.set_program_workdir(program_id, body.workdir)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/replan")
    def replan(program_id: str) -> dict:
        try:
            return service.replan(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.get("/programs/{program_id}/chat")
    def get_chat(program_id: str) -> list[dict]:
        try:
            return service.list_chat(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/chat")
    def post_chat(program_id: str, body: ChatIn) -> dict:
        try:
            return service.chat(program_id, body.message)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

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

    @api.get("/programs/{program_id}/ideas")
    def list_ideas(program_id: str) -> dict:
        try:
            return service.list_ideas(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/ideas", status_code=201)
    def add_idea(program_id: str, body: IdeaIn) -> dict:
        try:
            return service.add_idea(program_id, body.text, source="human")
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.delete("/programs/{program_id}/ideas/{idea_id}", status_code=204)
    def delete_idea(program_id: str, idea_id: str) -> Response:
        try:
            service.delete_idea(program_id, idea_id, by="human")
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {program_id}/{idea_id}")
        return Response(status_code=204)

    @api.post("/programs/{program_id}/ideas/{idea_id}/pin")
    def pin_idea(program_id: str, idea_id: str, body: IdeaPinIn) -> dict:
        try:
            return service.set_idea_pin(program_id, idea_id, body.pinned)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {program_id}/{idea_id}")

    @api.post("/programs/{program_id}/ideas/{idea_id}/demote")
    def demote_idea(program_id: str, idea_id: str, body: IdeaDemoteIn) -> dict:
        try:
            return service.set_idea_demoted(program_id, idea_id, body.demoted)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {program_id}/{idea_id}")

    @api.post("/programs/{program_id}/ideas/{idea_id}/comments", status_code=201)
    def comment_idea(program_id: str, idea_id: str, body: GuidanceIn) -> dict:
        try:
            return service.add_idea_comment(program_id, idea_id, body.text)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {program_id}/{idea_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

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
            candidate = (ui_dir / full_path).resolve()
            ui_root = ui_dir.resolve()
            if (
                full_path
                and candidate.is_file()
                and (candidate == ui_root or ui_root in candidate.parents)
            ):
                return FileResponse(candidate)
            return FileResponse(index)
    return app


def main() -> None:
    """Console entry point: run the HTTP API under uvicorn."""
    host = os.environ.get("COSCIENCE_HOST", "0.0.0.0")
    port = int(os.environ.get("COSCIENCE_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
