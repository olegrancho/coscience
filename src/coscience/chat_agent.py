"""Interactive PM chat as a resumable, tool-enabled Claude session per thread.

Each thread is one `claude -p` session: continuity across turns via
--session-id / --resume, running in the program's workdir. Read-only scope
whitelists explore/read tools; full scope bypasses permissions (a conversational
worker that can run commands and edit files). Turns are launched detached and
stream into turn.out, collected lazily when turn.exit appears — mirroring the
sprint worker rather than blocking the HTTP request."""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from coscience.executor import launch_detached

# Read-only scope: explore + read, no writes/bash. Anything else needs a
# permission it can't get in headless mode, so it simply can't mutate the tree.
_READ_TOOLS = ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch"]


def resolve_workdir(substrate, workdir: str) -> str:
    """The cwd for the chat session: the program's project folder if set and it
    exists, else the control repo (mirrors the worker / PM reasoner)."""
    if workdir:
        p = os.path.expanduser(workdir)
        if os.path.isdir(p):
            return p
    return str(substrate.repo_root)


def render_preamble(context, scope: str) -> str:
    """Sent once, on the first turn (a resumed session retains it). Establishes
    the PM role, the program context, and what tools the session may use."""
    def _lines(items, fmt):
        return "\n".join(fmt(i) for i in items) or "(none)"
    open_block = _lines(context.open_sprints, lambda s: f"- {s['id']} [{s['status']}]: {s['goals']}")
    done_block = _lines(context.completed, lambda s: f"- {s['id']}: {s['goals']} -> {s['result']}")
    ideas_block = _lines(context.ideas, lambda i: f"- {i['text']}")
    guidance_block = _lines(context.human_guidance, lambda g: f"- {g}")
    scope_note = (
        "TOOLS — READ-ONLY: you may explore the working directory and read files to "
        "answer with real evidence, but you cannot run commands that change anything "
        "or edit files."
        if scope == "read" else
        "TOOLS — FULL: you may run commands and create/edit files in the working "
        "directory, acting as a hands-on research assistant. Make changes only when "
        "the conversation clearly calls for it, and say what you did.")
    # Full scope can start processes, so it must know its session is disposable:
    # a backgrounded task or a "watch it and tell me when done" plan is killed the
    # moment this turn's process exits. The only resilient path is OS-level detach.
    longrun_note = "" if scope == "read" else """
LONG-RUNNING WORK — YOUR SESSION IS EPHEMERAL:
This chat turn is a one-shot process that ENDS the instant you send your reply. Anything
you launch as a background task, and anything that depends on a watcher/notification/hook
firing later to finish or check it, is KILLED when the turn ends — it will NEVER complete.
Do not start background tasks and rely on being re-invoked to collect them; that mechanism
does not exist here.
For work that outlasts a single reply, do ONE of these instead:
- If it finishes in seconds, run it SYNCHRONOUSLY and wait for it in THIS turn before replying.
- If it is genuinely long, DETACH it from your session at the OS level so it survives on its
  own: `nohup <cmd> > <name>.log 2>&1 & disown` (or `setsid ...`), writing its log into this
  working directory. Then in your reply state the exact command, the logfile path, and the
  PID. On a LATER turn, re-read that logfile to check progress — never assume something
  watched it for you.
- Best for substantial research jobs: propose a SPRINT rather than an ad-hoc background task.
  Sprints are the platform's durable, resumable mechanism built exactly for work that spans
  many sessions.
"""
    return f"""You are the PM (planning) agent for a research program, in a direct chat with the
human overseer. Answer clearly and use your tools when it helps. Your session runs in
this program's working directory.

{scope_note}
{longrun_note}

PROGRAM GOALS:
{context.goals}

OPEN SPRINTS (proposed / approved / queued / running):
{open_block}

COMPLETED SPRINTS AND RESULTS:
{done_block}

IDEA POOL:
{ideas_block}

STANDING GUIDANCE:
{guidance_block}
"""


def scope_change_notice(scope: str) -> str:
    """Prepended to the next turn's message when a thread's scope changed mid-session.
    The preamble (with its TOOLS line) is only sent on the first turn, so a resumed
    session otherwise keeps believing it has the original scope. This tells it."""
    if scope == "full":
        return (
            "[SYSTEM] Your tool scope for this chat has been changed to FULL: you may now run "
            "commands and create/edit files in the working directory (permission prompts are "
            "bypassed). You were previously read-only — act on this new capability when the "
            "conversation calls for it, and say what you did. Note: your session is ephemeral — "
            "it ends when you reply. Do not start background tasks that rely on a watcher to "
            "finish them; run short work synchronously, detach long work at the OS level "
            "(nohup/setsid into a logfile), or propose a sprint.")
    return (
        "[SYSTEM] Your tool scope for this chat has been changed to READ-ONLY: you may explore "
        "and read files, but can no longer run commands or edit files.")


def _turn_shell(claude_bin, prompt, scope, session_id, resume, model, out, exitf) -> str:
    parts = [claude_bin, "-p", shlex.quote(prompt)]
    parts += ["--resume", shlex.quote(session_id)] if resume else ["--session-id", shlex.quote(session_id)]
    if model:
        parts += ["--model", shlex.quote(model)]
    if scope == "full":
        parts += ["--dangerously-skip-permissions"]
    else:
        parts += ["--allowedTools", *_READ_TOOLS]
    parts += ["--output-format", "stream-json", "--verbose"]
    return (" ".join(parts)
            + f" > {shlex.quote(str(out))} 2>&1; echo $? > {shlex.quote(str(exitf))}")


def launch_turn(thread_dir: Path, workdir: str, prompt: str, scope: str,
                session_id: str, resume: bool, model: str = "",
                claude_bin: str = "claude") -> str:
    """Launch one detached chat turn; return its process token."""
    thread_dir.mkdir(parents=True, exist_ok=True)
    out, exitf = thread_dir / "turn.out", thread_dir / "turn.exit"
    for f in (out, exitf):
        if f.exists():
            f.unlink()
    cmd = _turn_shell(claude_bin, prompt, scope, session_id, resume, model, out, exitf)
    return launch_detached(cmd, cwd=workdir)


def collect_turn(thread_dir: Path) -> tuple[str, str, str]:
    """Return (reply_text, session_id, status). status is 'running' (no exit yet),
    'ok', or 'failed'. On a clean stream we unwrap the final result event's text and
    session id; on a non-JSON exit we return the raw text so errors stay visible."""
    out, exitf = thread_dir / "turn.out", thread_dir / "turn.exit"
    raw = out.read_text().strip() if out.exists() else ""
    if not exitf.exists():
        return "", "", "running"
    try:
        code = int((exitf.read_text().strip() or "1"))
    except (ValueError, OSError):
        code = 1
    result = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            result = ev
    status = "ok" if code == 0 else "failed"
    if result is None:
        return (raw or "(no output)"), "", status
    return str(result.get("result") or ""), str(result.get("session_id") or ""), status
