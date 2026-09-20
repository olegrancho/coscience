"""Escalation (O8): a sprint stops and asks for help.

The worker agent writes escalate.json and ends its turn, or the dispatcher raises the same
record when a sprint sleeps on a job whose host went quiet. The sprint is held in
`escalated` with its lease and job; nobody relaunches the agent until the PM or a human
answers through `answer`."""
from __future__ import annotations

import json
import time
from pathlib import Path

from coscience import host_health, threads
from coscience.models import SprintStatus, set_status

FIELDS = ("what", "tried", "may_have_broken_something", "needs", "host_notes")

ACTIONS = ("resume", "reallocate", "to_human", "stop")


def read_escalate_json(sprint_dir) -> dict | None:
    """The agent's escalation, or None. Presence is the signal: an unreadable file still
    escalates, saying so, rather than being taken for a normal exit."""
    f = Path(sprint_dir) / "escalate.json"
    if not f.is_file():
        return None
    try:
        d = json.loads(f.read_text())
        if not isinstance(d, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return {"what": f"escalate.json could not be read ({exc}); the agent asked for help",
                "tried": "", "may_have_broken_something": False, "needs": "", "host_notes": ""}
    return {"what": str(d.get("what") or "").strip() or "(no description)",
            "tried": str(d.get("tried") or "").strip(),
            "may_have_broken_something": d.get("may_have_broken_something") is True,
            "needs": str(d.get("needs") or "").strip(),
            "host_notes": str(d.get("host_notes") or "").strip()}


def render(record: dict) -> str:
    lines = [f"**Escalation** raised by the {'platform' if record.get('by') == 'dispatcher' else 'worker agent'}"
             + (f" on {record['host']}" if record.get("host") else ""),
             "", f"**What happened:** {record.get('what', '')}"]
    if record.get("tried"):
        lines.append(f"**Tried:** {record['tried']}")
    if record.get("may_have_broken_something"):
        lines.append("**The agent believes it may have damaged the host or the program's data.**")
    if record.get("needs"):
        lines.append(f"**Needs:** {record['needs']}")
    return "\n".join(lines)


def raise_escalation(substrate, sprint, progress, record: dict, now: float | None = None) -> None:
    now = time.time() if now is None else now
    level = "human" if progress.pm_answered else "pm"
    by = str(record.get("by") or "agent")
    th = threads.new_thread(level, render(record), by, role="worker", now=now)
    th["kind"] = "escalation"
    sprint.threads.append(th)
    set_status(sprint, SprintStatus.ESCALATED, by=by, action="escalate")
    progress.escalation = {"by": by, "at": now, "host": str(record.get("host") or ""),
                           "level": level, "thread_id": th["id"],
                           **{k: record.get(k, "") for k in FIELDS}}
    substrate.save_sprint(sprint)
    substrate.save_progress(progress)
    host_notes = str(record.get("host_notes") or "").strip()
    if host_notes and sprint.program:
        # Filed once, here, where the escalation itself is recorded: what the agent
        # learned about the machine outlives this escalation and is the next sprint's
        # to read, whatever answer the PM gives (O9). It goes AFTER the escalation is
        # saved, and never raises: this is the red button, and it must still be pulled
        # when a full disk or a host name the notes layer refuses stops the report.
        try:
            substrate.add_host_report(sprint.program, sprint_id=sprint.id,
                                      host=progress.host or "local", text=host_notes,
                                      source="escalation", now=now)
        except (OSError, ValueError):
            pass


def move_targets(pool, program, current_host: str, repo_root) -> list[str]:
    """Hosts a reallocate/hosts_allowed listing may offer: placeable, allowed for the
    program, not drained, not quiet (checked fresh — the caller's `pool` may carry no
    `closed` entries of its own), and not the sprint's current host."""
    quiet_now = host_health.quiet(host_health.load(repo_root), time.time())
    return [h.name for h in pool.grantable_hosts(program)
            if h.name not in quiet_now and h.name != current_host]


def answer(substrate, sprint_id: str, action: str, *, instructions: str = "", host: str = "",
           by: str = "", pool=None, now: float | None = None, thread_id: str = "") -> str:
    """Apply a PM's or a human's answer to an escalated sprint. Returns "" when the
    answer was applied, else the reason it was not — never raises for a bad answer;
    the caller (service/PM reasoner) surfaces the reason instead."""
    now = time.time() if now is None else now
    if not (Path(substrate.sprint_dir(sprint_id)) / "sprint.md").is_file():
        return "no such sprint"
    sprint = substrate.load_sprint(sprint_id)
    progress = substrate.load_progress(sprint_id)
    if sprint.status != SprintStatus.ESCALATED:
        return f"{sprint_id} is not escalated (it is {sprint.status.value})"

    if progress.stop_requested:
        return "a stop is already pending for this sprint"
    current_thread_id = str((progress.escalation or {}).get("thread_id") or "")
    if thread_id and thread_id != current_thread_id:
        return "this answer is for an earlier escalation"

    level = str((progress.escalation or {}).get("level") or "pm")
    if by == "pm":
        if action == "stop":
            return "only a human can stop a sprint"
        if level == "human":
            return "this escalation is with a human; the PM does not answer it"
    elif action == "to_human":
        return "already with a human"
    if action not in ACTIONS:
        return f"unknown action {action!r}"

    if action == "reallocate":
        from coscience.resources import load_pool
        pool = pool or load_pool(substrate.repo_root)
        target = pool.host(host)
        if target is None:
            return f"no host {host!r} in the pool"
        if host == progress.host:
            return "reallocate to the same host it is on; use resume instead"
        if not target.placeable or not target.allows(sprint.program):
            return f"program {sprint.program} may not use {host}"
        if host not in move_targets(pool, sprint.program, progress.host, substrate.repo_root):
            return f"{host} takes no new work right now (being removed, drained or not answering)"

    role = "pm" if by == "pm" else "human"
    thread_id = str((progress.escalation or {}).get("thread_id") or "")
    thread = next((t for t in sprint.threads if t.get("id") == thread_id), None)
    text = f"{action} to {host}: {instructions}" if action == "reallocate" \
        else (f"{action}: {instructions}" if instructions else action)
    if thread is not None:
        threads.append(thread, role, text, by, now=now)

    if action in ("resume", "reallocate"):
        progress.resume_note = instructions
        if action == "reallocate":
            progress.reallocate_to = host
        set_status(sprint, SprintStatus.EXECUTING, by=by, action=action)
        progress.pm_answered = (by == "pm")
        progress.escalation = {}
        if thread is not None:
            thread["status"] = "complete"
    elif action == "to_human":
        progress.escalation["level"] = "human"
        if thread is not None:
            thread["target"] = "human"
    elif action == "stop":
        progress.stop_requested = True
        if thread is not None:
            thread["status"] = "complete"

    substrate.save_sprint(sprint)
    substrate.save_progress(progress)
    return ""
