"""A shared feedback-thread structure: a human↔agent conversation stored inline
(on a sprint, idea, or program guidance). Plain dicts, mirroring comments/votes."""
from __future__ import annotations

from uuid import uuid4

AGENT_ROLES = ("pm", "worker")


def new_thread(target: str, text: str, by: str, *, role: str = "human",
               now: float, tid: str | None = None) -> dict:
    t = {"id": tid or uuid4().hex[:8], "target": target, "status": "open",
         "agent_unseen": False, "created_at": now, "messages": []}
    append(t, role, text, by, now=now)
    return t


def append(thread: dict, role: str, text: str, by: str, *, now: float) -> None:
    thread.setdefault("messages", []).append(
        {"role": role, "text": str(text), "by": str(by or ""), "at": now})
    if role == "human":
        if thread.get("status") == "complete":
            thread["status"] = "open"
    elif role in AGENT_ROLES:
        thread["agent_unseen"] = True


def needs_reply(thread: dict) -> bool:
    msgs = thread.get("messages") or []
    return thread.get("status") == "open" and bool(msgs) and msgs[-1]["role"] == "human"


def adapt_legacy(comment: dict, default_target: str, *, now: float) -> dict:
    return {"id": str(comment.get("id") or uuid4().hex[:8]),
            "target": str(comment.get("target") or default_target),
            "status": "open", "agent_unseen": False,
            "created_at": float(comment.get("added_at", now)),
            "messages": [{"role": "human", "text": str(comment.get("text", "")),
                          "by": str(comment.get("by", "")),
                          "at": float(comment.get("added_at", now))}]}


def public(thread: dict) -> dict:
    return {"id": thread["id"], "target": thread.get("target", "pm"),
            "status": thread.get("status", "open"),
            "agent_unseen": bool(thread.get("agent_unseen", False)),
            "created_at": thread.get("created_at", 0.0),
            "messages": [{"role": m["role"], "text": m["text"], "by": m.get("by", ""),
                          "at": m["at"]} for m in thread.get("messages", [])]}
