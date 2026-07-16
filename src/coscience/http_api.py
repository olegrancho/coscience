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
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coscience import auth
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
    text: str = Field(min_length=1)
    thread_id: str = ""            # append to an existing thread instead of starting one


class ChatIn(BaseModel):
    message: str = Field(min_length=1)


class ChatCreateIn(BaseModel):
    title: str = ""


class ChatPatchIn(BaseModel):
    title: str | None = None
    scope: str | None = None       # "read" | "full"


class IdeaIn(BaseModel):
    text: str = Field(min_length=1)


class IdeaPinIn(BaseModel):
    pinned: bool


class IdeaDemoteIn(BaseModel):
    demoted: bool = True


class IdeaCommentIn(BaseModel):
    text: str = Field(min_length=1)
    thread_id: str = ""            # append to an existing thread instead of starting one


class SprintCommentIn(BaseModel):
    text: str = Field(min_length=1)
    target: str = "worker"          # 'worker' (steers the agent) or 'pm' (steers the planner)
    thread_id: str = ""            # append to an existing thread instead of starting one


class LoginIn(BaseModel):
    username: str


def build_app(service: Service, title: str = "Co-Science Platform") -> FastAPI:
    app = FastAPI(title=title, version="0.0.0")

    COOKIE = "coscience_user"

    def _resolve_user(request: Request) -> "tuple[auth.User | None, bool]":
        """(user, required) without raising. required=False means auth is disabled
        (empty registry). user is None when disabled OR when no valid cookie."""
        users = auth.load_users(service.repo_root)
        if not users:
            return None, False                            # auth disabled (empty registry)
        uname = auth.verify_cookie(request.cookies.get(COOKIE, ""), service.repo_root)
        return users.get(uname), True

    def current_user(request: Request) -> "auth.User | None":
        user, required = _resolve_user(request)
        if required and user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return user                                       # None only when auth disabled

    pub = APIRouter(prefix="/api")                          # open endpoints
    api = APIRouter(prefix="/api", dependencies=[Depends(current_user)])  # gated

    @pub.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @pub.get("/version")
    def version() -> dict:
        return {"sha": server_version()}

    @pub.get("/users")
    def list_users() -> list[dict]:
        return [{"username": u.username, "name": u.name, "initials": u.initials}
                for u in auth.load_users(service.repo_root).values()]

    @pub.get("/me")
    def me(request: Request) -> dict:
        # Soft endpoint — ALWAYS 200 (never 401), so the frontend gate reads a clean
        # {user, required} without treating logged-out as a retryable error.
        user, required = _resolve_user(request)
        return {"user": None if user is None else
                {"username": user.username, "name": user.name, "initials": user.initials},
                "required": required}

    @pub.post("/login")
    def login(body: LoginIn, response: Response) -> dict:
        u = auth.load_users(service.repo_root).get(body.username.strip())
        if u is None:
            raise HTTPException(status_code=401, detail="unknown user")
        response.set_cookie(COOKIE, auth.make_cookie(u.username, service.repo_root),
                            httponly=True, samesite="lax", path="/",
                            max_age=60 * 60 * 24 * 30)
        return {"username": u.username, "name": u.name, "initials": u.initials}

    @pub.post("/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

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

    @api.post("/sprints/{sprint_id}/wake")
    def wake_sprint(sprint_id: str,
                    user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.wake_sprint(sprint_id)
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
    def comment_sprint(sprint_id: str, body: SprintCommentIn,
                       user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_sprint_comment(sprint_id, body.text, target=body.target,
                                              by=(user.username if user else ""),
                                              thread_id=body.thread_id)
        except NotFoundError as exc:
            missing = exc.args[0] if exc.args else sprint_id
            raise HTTPException(status_code=404, detail=f"not found: {missing}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/sprints/{sprint_id}/threads/{tid}/complete")
    def complete_sprint_thread(sprint_id: str, tid: str,
                               user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.complete_sprint_thread(sprint_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/sprints/{sprint_id}/threads/{tid}/seen")
    def seen_sprint_thread(sprint_id: str, tid: str,
                           user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.seen_sprint_thread(sprint_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/sprints/{sprint_id}/threads/{tid}/reopen")
    def reopen_sprint_thread(sprint_id: str, tid: str,
                             user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.reopen_sprint_thread(sprint_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.delete("/sprints/{sprint_id}/threads/{tid}", status_code=204)
    def delete_sprint_thread(sprint_id: str, tid: str,
                             user: "auth.User | None" = Depends(current_user)) -> Response:
        try:
            service.delete_sprint_thread(sprint_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
        return Response(status_code=204)

    @api.post("/sprints/{sprint_id}/approve")
    def approve_sprint(sprint_id: str,
                       user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            service.approve_sprint(sprint_id, by=(user.username if user else ""))
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/run")
    def run_sprint(sprint_id: str,
                   user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            service.run_sprint(sprint_id, by=(user.username if user else ""))
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/send_back")
    def send_back_sprint(sprint_id: str,
                         user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            service.send_back_sprint(sprint_id, by=(user.username if user else ""))
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/vote")
    def vote_sprint(sprint_id: str, body: VoteIn,
                    user: "auth.User | None" = Depends(current_user)) -> dict:
        by = user.username if user else body.by
        try:
            return service.vote_sprint(sprint_id, by, body.value)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/sprints/{sprint_id}/reject")
    def reject_sprint(sprint_id: str,
                      user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            service.reject_sprint(sprint_id, by=(user.username if user else ""))
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service.get_sprint(sprint_id)

    @api.post("/sprints/{sprint_id}/demote")
    def demote_sprint(sprint_id: str,
                      user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.demote_sprint(sprint_id, by=(user.username if user else ""))
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

    @api.post("/programs/{program_id}/ideas/{mode}")
    def pm_directive(program_id: str, mode: str) -> dict:
        if mode not in ("compress", "brainstorm"):
            raise HTTPException(status_code=404, detail=f"unknown action: {mode}")
        try:
            return service.run_pm_directive(program_id, mode)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.get("/programs/{program_id}/chats")
    def list_chats(program_id: str) -> list[dict]:
        try:
            return service.list_chats(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/chats", status_code=201)
    def create_chat(program_id: str, body: ChatCreateIn) -> dict:
        try:
            return service.create_chat(program_id, body.title)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.get("/programs/{program_id}/chats/{thread_id}")
    def get_chat_thread(program_id: str, thread_id: str) -> dict:
        try:
            return service.get_chat_thread(program_id, thread_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="chat not found")

    @api.post("/programs/{program_id}/chats/{thread_id}/messages")
    def post_chat_message(program_id: str, thread_id: str, body: ChatIn,
                          user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.post_chat_message(program_id, thread_id, body.message,
                                             by=(user.username if user else ""))
        except NotFoundError:
            raise HTTPException(status_code=404, detail="chat not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.patch("/programs/{program_id}/chats/{thread_id}")
    def patch_chat(program_id: str, thread_id: str, body: ChatPatchIn) -> dict:
        try:
            if body.scope is not None:
                service.set_chat_scope(program_id, thread_id, body.scope)
            if body.title is not None:
                return service.rename_chat(program_id, thread_id, body.title)
            return service.get_chat_thread(program_id, thread_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="chat not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.delete("/programs/{program_id}/chats/{thread_id}", status_code=204)
    def delete_chat(program_id: str, thread_id: str) -> None:
        try:
            service.delete_chat(program_id, thread_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="chat not found")

    @api.get("/programs/{program_id}/guidance")
    def list_guidance(program_id: str) -> list[dict]:
        try:
            return service.list_guidance(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/guidance", status_code=201)
    def add_guidance(program_id: str, body: GuidanceIn,
                     user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_guidance(program_id, body.text,
                                        by=(user.username if user else ""),
                                        thread_id=body.thread_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.delete("/programs/{program_id}/guidance/{note_id}", status_code=204)
    def remove_guidance(program_id: str, note_id: str) -> Response:
        try:
            service.remove_guidance(program_id, note_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        return Response(status_code=204)

    @api.post("/programs/{program_id}/guidance/{tid}/complete")
    def complete_guidance_thread(program_id: str, tid: str,
                                 user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.complete_guidance_thread(program_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/programs/{program_id}/guidance/{tid}/seen")
    def seen_guidance_thread(program_id: str, tid: str,
                             user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.seen_guidance_thread(program_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/programs/{program_id}/guidance/{tid}/reopen")
    def reopen_guidance_thread(program_id: str, tid: str,
                               user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.reopen_guidance_thread(program_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.get("/programs/{program_id}/ideas")
    def list_ideas(program_id: str) -> dict:
        try:
            return service.list_ideas(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")

    @api.post("/programs/{program_id}/ideas", status_code=201)
    def add_idea(program_id: str, body: IdeaIn,
                user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_idea(program_id, body.text, source="human",
                                    by=(user.username if user else ""))
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
    def comment_idea(program_id: str, idea_id: str, body: IdeaCommentIn,
                     user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_idea_comment(program_id, idea_id, body.text,
                                            by=(user.username if user else ""),
                                            thread_id=body.thread_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {program_id}/{idea_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/programs/{program_id}/ideas/{idea_id}/threads/{tid}/complete")
    def complete_idea_thread(program_id: str, idea_id: str, tid: str,
                             user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.complete_idea_thread(program_id, idea_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/programs/{program_id}/ideas/{idea_id}/threads/{tid}/seen")
    def seen_idea_thread(program_id: str, idea_id: str, tid: str,
                         user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.seen_idea_thread(program_id, idea_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/programs/{program_id}/ideas/{idea_id}/threads/{tid}/reopen")
    def reopen_idea_thread(program_id: str, idea_id: str, tid: str,
                           user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.reopen_idea_thread(program_id, idea_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.delete("/programs/{program_id}/ideas/{idea_id}/threads/{tid}", status_code=204)
    def delete_idea_thread(program_id: str, idea_id: str, tid: str,
                           user: "auth.User | None" = Depends(current_user)) -> Response:
        try:
            service.delete_idea_thread(program_id, idea_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
        return Response(status_code=204)

    app.include_router(pub)
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
