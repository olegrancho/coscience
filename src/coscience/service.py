"""Transport-agnostic service API over the substrate + ledger.

Every method returns JSON-serialisable plain data so the MCP and HTTP layers
can hand results straight to clients.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus, Program, ProgramStatus, Idea
from coscience.resources import ResourcePool, load_pool
from coscience.substrate import Substrate


def service_from_env() -> "Service":
    """Construct a Service from COSCIENCE_REPO (default: current directory)."""
    repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))
    return Service(repo_root)


class NotFoundError(KeyError):
    """A requested sprint or result does not exist."""


class Service:
    def __init__(self, repo_root, pool: ResourcePool | None = None):
        self.repo_root = Path(repo_root)
        self.substrate = Substrate(self.repo_root)
        self.pool = pool if pool is not None else load_pool(self.repo_root)

    def _ledger(self) -> Ledger:
        ledger = Ledger(self.pool, self.repo_root / ".coscience" / "leases.json")
        ledger.load()
        return ledger

    def _load_sprint(self, sprint_id: str) -> Sprint:
        if not (self.substrate.sprint_dir(sprint_id) / "sprint.md").is_file():
            raise NotFoundError(sprint_id)
        return self.substrate.load_sprint(sprint_id)

    # --- sprints ---
    def submit_sprint(self, *, id: str, goals: str, plan: list[str],
                      program: str | None = None, priority: int = 0,
                      preemptible: bool = True, resources_required: dict | None = None,
                      status: str = "proposed") -> str:
        if not plan:
            raise ValueError("plan must have at least one suggested step")
        if (self.substrate.sprint_dir(id) / "sprint.md").is_file():
            raise ValueError(f"sprint {id} already exists")
        sprint = Sprint(
            id=id,
            status=SprintStatus(status),
            goals=goals,
            plan=[str(step) for step in plan],
            program=program,
            resources_required={k: float(v) for k, v in (resources_required or {}).items()},
            priority=priority,
            preemptible=preemptible,
        )
        self.substrate.save_sprint(sprint)
        return id

    def approve_sprint(self, sprint_id: str) -> None:
        """Human authorization: proposed -> approved. Cleared to run, but held
        until released with run_sprint (by you or the PM)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PROPOSED:
            raise ValueError(f"can only approve a proposed sprint; {sprint_id} is {sprint.status.value}")
        sprint.status = SprintStatus.APPROVED
        self.substrate.save_sprint(sprint)

    def run_sprint(self, sprint_id: str) -> None:
        """Release an approved sprint to the scheduler: approved -> queued. The
        dispatcher runs it as soon as a resource slot frees (it may wait in queue)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.APPROVED:
            raise ValueError(f"can only run an approved sprint; {sprint_id} is {sprint.status.value}")
        sprint.status = SprintStatus.QUEUED
        self.substrate.save_sprint(sprint)

    def send_back_sprint(self, sprint_id: str) -> None:
        """Return an approved sprint to proposed for reconsideration."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.APPROVED:
            raise ValueError(f"can only send back an approved sprint; {sprint_id} is {sprint.status.value}")
        sprint.status = SprintStatus.PROPOSED
        self.substrate.save_sprint(sprint)

    _REJECTABLE = (SprintStatus.PROPOSED, SprintStatus.APPROVED, SprintStatus.QUEUED)

    def reject_sprint(self, sprint_id: str) -> None:
        """Cancel a pre-execution sprint (proposed / approved / queued)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status not in self._REJECTABLE:
            raise ValueError(f"can only cancel a pre-run sprint; {sprint_id} is {sprint.status.value}")
        sprint.status = SprintStatus.CANCELED
        self.substrate.save_sprint(sprint)

    def vote_sprint(self, sprint_id: str, by: str, value: int) -> dict:
        """Record a 👍/👎 on a sprint. `value` is +1, -1, or 0 (clear). One vote
        per `by` (a browser id) — re-voting the same way clears it (toggle),
        voting the other way switches. Returns the tally."""
        by = str(by).strip()
        if not by:
            raise ValueError("voter id is required")
        if value not in (-1, 0, 1):
            raise ValueError("vote must be +1, -1, or 0")
        sprint = self._load_sprint(sprint_id)
        prior = next((v for v in sprint.votes if v["by"] == by), None)
        sprint.votes = [v for v in sprint.votes if v["by"] != by]
        # toggle: same direction again -> cleared; else set the new direction
        if value != 0 and not (prior and prior["value"] == value):
            sprint.votes.append({"by": by, "value": value, "at": time.time()})
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: vote")
        return self._vote_tally(sprint, by)

    @staticmethod
    def _vote_tally(sprint, viewer: str = "") -> dict:
        up = sum(1 for v in sprint.votes if v["value"] > 0)
        down = sum(1 for v in sprint.votes if v["value"] < 0)
        mine = next((v["value"] for v in sprint.votes if v["by"] == viewer), 0) if viewer else 0
        return {"up": up, "down": down, "mine": mine}

    def edit_sprint(self, sprint_id: str, *, goals=None, plan=None, priority=None,
                    resources_required=None, preemptible=None, model=None) -> None:
        sprint = self._load_sprint(sprint_id)
        st = sprint.status
        if st in (SprintStatus.DONE, SprintStatus.CANCELED):
            raise ValueError(f"{sprint_id} is {st.value} and is read-only")
        if (goals is not None or plan is not None) and st != SprintStatus.PROPOSED:
            raise ValueError("goals/plan are editable only while proposed")
        if plan is not None and len(plan) == 0:
            raise ValueError("plan must have at least one suggested step")
        if goals is not None:
            sprint.goals = goals
        if plan is not None:
            sprint.plan = [str(s) for s in plan]
        if priority is not None:
            sprint.priority = priority
        if resources_required is not None:
            sprint.resources_required = {k: float(v) for k, v in resources_required.items()}
        if preemptible is not None:
            sprint.preemptible = preemptible
        if model is not None and model != sprint.model:
            # The model is switchable at any time. A detached agent can't change model
            # mid-process, so if one is already running we stop it; the next dispatch
            # beat relaunches on the new model and resumes from the scratchpad.
            sprint.model = str(model)
            self._restart_agent_for_model(sprint_id)
        self.substrate.save_sprint(sprint)

    def _restart_agent_for_model(self, sprint_id: str) -> None:
        from coscience.executor import terminate_detached
        progress = self.substrate.load_progress(sprint_id)
        if not progress.agent_token:
            return
        try:
            terminate_detached(progress.agent_token)
        except Exception:
            pass
        progress.agent_token = ""
        self.substrate.save_progress(progress)

    def list_sprints(self, status: str | None = None) -> list[dict]:
        wanted = SprintStatus(status) if status is not None else None
        rows = []
        for sprint in self.substrate.iter_sprints(status=wanted):
            started = None
            activity = None
            if sprint.status == SprintStatus.EXECUTING:
                started = self.substrate.load_progress(sprint.id).started_at
                activity = self._activity(sprint.id)
            rows.append({
                "id": sprint.id,
                "status": sprint.status.value,
                "title": sprint.title,
                "summary": sprint.summary,
                "goals": sprint.goals,
                "program": sprint.program,
                "priority": sprint.priority,
                "steps": len(sprint.plan),
                "results": list(sprint.results),
                "rationale": sprint.rationale,
                "resources_required": sprint.resources_required,
                "started_at": started,
                "model": sprint.model,
                "activity": activity,
                "votes": self._vote_tally(sprint),
            })
        return rows

    def _activity(self, sprint_id: str) -> dict | None:
        from coscience.claude_executor import read_activity
        return read_activity(self.substrate.sprint_dir(sprint_id))

    def get_sprint(self, sprint_id: str, viewer: str = "") -> dict:
        sprint = self._load_sprint(sprint_id)
        progress = self.substrate.load_progress(sprint_id)
        lease = self._ledger().lease_for(sprint_id)
        return {
            "id": sprint.id,
            "status": sprint.status.value,
            "title": sprint.title,
            "summary": sprint.summary,
            "goals": sprint.goals,
            "priority": sprint.priority,
            "preemptible": sprint.preemptible,
            "resources_required": sprint.resources_required,
            "rationale": sprint.rationale,
            "program": sprint.program,
            "model": sprint.model,
            "results": list(sprint.results),
            "plan": list(sprint.plan),
            "comments": list(sprint.comments),
            "votes": self._vote_tally(sprint, viewer),
            "agent_running": bool(progress.agent_token),
            "started_at": progress.started_at,
            "activity": self._activity(sprint_id) if sprint.status == SprintStatus.EXECUTING else None,
            "error": progress.last_error if sprint.status == SprintStatus.FAILED else "",
            "lease": None if lease is None else {
                "id": lease.id, "sprint_id": lease.sprint_id, "amounts": lease.amounts,
                "granted_at": lease.granted_at, "expires_at": lease.expires_at,
                "priority": lease.priority, "preemptible": lease.preemptible,
            },
        }

    def usage_stats(self) -> dict:
        """Claude usage for the dashboard: the rolling 5h/weekly budget plus how
        many calls the PM and worker have each made (total / last hour / last day)."""
        from coscience import usage_meter
        return {"budget": usage_meter.read_budget(),
                "runs": usage_meter.run_stats(self.repo_root)}

    def add_sprint_comment(self, sprint_id: str, text: str, target: str = "worker") -> dict:
        """Append a human comment to a sprint. Allowed in any status — it's
        feedback, not an edit. `target` routes it: 'worker' (the running agent
        reads it as direction) or 'pm' (the planner reads it and may revise the
        sprint or propose a follow-up)."""
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        if target not in ("worker", "pm"):
            raise ValueError("target must be 'worker' or 'pm'")
        sprint = self._load_sprint(sprint_id)
        comment = {"id": uuid4().hex[:8], "text": text, "added_at": time.time(), "target": target}
        sprint.comments.append(comment)
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: comment added ({target})")
        return comment

    # Files surfaced in the UI as the agent's "working documents", with a
    # friendly label + kind and the order they should display in.
    _DOC_LABELS = {
        "scratchpad.md": ("Scratchpad", "scratchpad"),
        "agent.out": ("Agent log", "log"),
        "instructions.md": ("Instructions", "instructions"),
    }
    _DOC_ORDER = {"scratchpad": 0, "log": 1, "instructions": 2, "artifact": 3}
    # Plumbing that isn't a "document": the spec is shown as structured fields,
    # progress holds the process token, agent.exit is just an exit code.
    _DOC_HIDDEN = {"sprint.md", "progress.md", "agent.exit"}
    _DOC_MAX_BYTES = 256 * 1024

    def list_sprint_files(self, sprint_id: str) -> list[dict]:
        """The agent's working documents for a sprint — scratchpad, log,
        instructions, and any artifacts it produced — for display in the UI.

        Reads only files directly in the sprint directory. Large files are
        tailed (the recent end matters most for logs); binaries are flagged
        without content.
        """
        self._load_sprint(sprint_id)  # raises NotFoundError for unknown sprints
        d = self.substrate.sprint_dir(sprint_id)
        docs: list[dict] = []
        for path in (d.iterdir() if d.is_dir() else []):
            if not path.is_file() or path.name.startswith(".") or path.name in self._DOC_HIDDEN:
                continue
            label, kind = self._DOC_LABELS.get(path.name, (path.name, "artifact"))
            raw = path.read_bytes()
            size = len(raw)
            truncated = size > self._DOC_MAX_BYTES
            if truncated:
                raw = raw[-self._DOC_MAX_BYTES:]  # keep the tail — most relevant for logs
                nl = raw.find(b"\n")              # ...but start at a clean line boundary so the
                if nl != -1:                      # partial first line — and any UTF-8 codepoint
                    raw = raw[nl + 1:]            # split at the byte cut — isn't shown as garbage
            # A real binary has NUL bytes; text logs never do. Decoding a truncated text
            # tail with errors="replace" keeps it readable even if a codepoint got clipped
            # (rather than failing the whole decode and mis-flagging the log as binary).
            binary = b"\x00" in raw[:8192]
            content = "" if binary else raw.decode("utf-8", errors="replace")
            docs.append({"name": path.name, "label": label, "kind": kind,
                         "size": size, "content": content,
                         "truncated": truncated, "binary": binary})
        docs.sort(key=lambda f: (self._DOC_ORDER[f["kind"]], f["name"]))
        return docs

    def read_sprint_file(self, sprint_id: str, name: str) -> dict:
        """Full (untruncated) content of one sprint document — backs the UI's
        'show full log' toggle, where list_sprint_files tails large files.
        Path-guarded to the sprint directory (no traversal, no hidden files)."""
        self._load_sprint(sprint_id)  # raises NotFoundError for unknown sprints
        d = self.substrate.sprint_dir(sprint_id).resolve()
        path = (d / name).resolve()
        if (path.parent != d or not path.is_file()
                or path.name.startswith(".") or path.name in self._DOC_HIDDEN):
            raise NotFoundError(name)
        label, kind = self._DOC_LABELS.get(path.name, (path.name, "artifact"))
        raw = path.read_bytes()
        binary = b"\x00" in raw[:8192]
        content = "" if binary else raw.decode("utf-8", errors="replace")
        return {"name": path.name, "label": label, "kind": kind,
                "size": len(raw), "content": content,
                "truncated": False, "binary": binary}

    # --- programs (read-only) ---
    def list_programs(self, status: str | None = None) -> list[dict]:
        wanted = ProgramStatus(status) if status is not None else None
        return [{"id": p.id, "title": p.title, "status": p.status.value, "goals": p.goals}
                for p in self.substrate.iter_programs(status=wanted)]

    def _appeared_at(self, sprint: Sprint) -> float:
        """Sort key putting a program's sprints in creation order. Uses the
        stored created_at; for legacy sprints without it, falls back to the
        sprint.md modification time."""
        if sprint.created_at is not None:
            return sprint.created_at
        spec = self.substrate.sprint_dir(sprint.id) / "sprint.md"
        return spec.stat().st_mtime if spec.is_file() else 0.0

    def get_program(self, program_id: str) -> dict:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        p = self.substrate.load_program(program_id)
        pm = self.substrate.load_pm_state(program_id)
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        sprints.sort(key=self._appeared_at, reverse=True)  # newest first
        return {
            "id": p.id, "title": p.title, "status": p.status.value, "goals": p.goals,
            "pm_model": p.pm_model, "workdir": p.workdir,
            "report": self.substrate.load_report(program_id),
            "cycle": pm.cycle,
            "activations": list(reversed(pm.activations)),   # newest first, for the timeline
            "last_run": pm.last_run,
            "sprints": [{"id": s.id, "status": s.status.value, "goals": s.goals,
                         "title": s.title, "results": list(s.results), "model": s.model,
                         "votes": self._vote_tally(s)}
                        for s in sprints],
        }

    def set_program_status(self, program_id: str, status: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        new_status = ProgramStatus(status)  # raises ValueError on a bad value
        program = self.substrate.load_program(program_id)
        program.status = new_status
        self.substrate.save_program(program)

    def set_program_model(self, program_id: str, model: str) -> dict:
        """Set the Claude model the PM reasoner uses for this program ("" = default)."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.pm_model = str(model or "")
        self.substrate.save_program(program)
        return {"id": program_id, "pm_model": program.pm_model}

    def replan(self, program_id: str) -> dict:
        """Run one PM cycle for this program right now (forced) so a human edit or
        comment is acted on without waiting for the loop tick. The per-program lock
        inside pm_beat keeps this from racing the background loop; returns the beat
        summary (with `busy` if the loop was mid-cycle)."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        from coscience.pm_agent import pm_beat
        from coscience.pm_claude import ClaudeCodeReasoner
        from coscience.worker import claude_usage_ok
        return pm_beat(self.substrate, program_id, ClaudeCodeReasoner(),
                       usage_ok=claude_usage_ok, force=True)

    def set_program_workdir(self, program_id: str, workdir: str) -> dict:
        """Set the project folder this program's sprint agents run in ("" = control
        repo). Returns the stored value plus whether the path currently exists, so
        the UI can warn on a typo without blocking (the folder may appear later)."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        wd = str(workdir or "").strip()
        program = self.substrate.load_program(program_id)
        program.workdir = wd
        self.substrate.save_program(program)
        exists = bool(wd) and os.path.isdir(os.path.expanduser(wd))
        return {"id": program_id, "workdir": wd, "exists": exists}

    def _require_program(self, program_id: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)

    # --- PM chat (ask the planner clarifying questions; answer-only) ---
    def list_chat(self, program_id: str) -> list[dict]:
        self._require_program(program_id)
        return self.substrate.load_chat(program_id)

    def chat(self, program_id: str, message: str, chat_fn=None) -> dict:
        """Post a message to the PM chat and get its reply. `chat_fn(context, history,
        message) -> str` is injectable for tests; the default shells the reasoner with
        the program context. The thread is persisted (capped) in chat.md."""
        self._require_program(program_id)
        message = str(message).strip()
        if not message:
            raise ValueError("message is required")
        history = self.substrate.load_chat(program_id)
        from coscience.worker import claude_usage_ok
        if chat_fn is None and not claude_usage_ok():
            reply = "_(Claude usage is exhausted — please try again after the reset.)_"
        else:
            from coscience.pm_agent import gather_context
            from coscience.pm_claude import chat_reply
            context = gather_context(self.substrate, program_id)
            reply = (chat_fn or chat_reply)(context, list(history), message)
        now = time.time()
        history.append({"role": "user", "text": message, "at": now})
        history.append({"role": "pm", "text": reply, "at": time.time()})
        history = history[-200:]                          # bound the stored thread
        self.substrate.save_chat(program_id, history)
        self.substrate.commit(f"program {program_id}: pm chat")
        return {"reply": reply, "messages": history}

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

    # --- ideas ---
    @staticmethod
    def _idea_public(i: Idea) -> dict:
        return {"id": i.id, "text": i.text, "source": i.source, "pinned": i.pinned,
                "protected": i.protected, "comments": list(i.comments),
                "created_at": i.created_at, "demoted": i.demoted}

    def demote_sprint(self, sprint_id: str) -> dict:
        """Demote a proposed/approved sprint into a non-promotable idea. The idea
        is flagged 'demoted' (the PM may not promote it back); a human can lift that.
        The sprint is canceled so it leaves the board."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status not in (SprintStatus.PROPOSED, SprintStatus.APPROVED):
            raise ValueError(
                f"only a proposed or approved sprint can be demoted (is {sprint.status.value})")
        if not sprint.program:
            raise ValueError("sprint has no program to hold the idea")
        summary, ideas = self.substrate.load_ideas(sprint.program)
        text = (sprint.title or sprint.goals or sprint.id).strip()
        idea = Idea(id=uuid4().hex[:8], text=text, source="human",
                    demoted=True, created_at=time.time())
        ideas.append(idea)
        self.substrate.save_ideas(sprint.program, summary, ideas)
        sprint.status = SprintStatus.CANCELED
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id} demoted to idea {idea.id}")
        return {"sprint_id": sprint_id, "idea": self._idea_public(idea)}

    def set_idea_demoted(self, program_id: str, idea_id: str, demoted: bool) -> dict:
        """Lift or set an idea's demoted status (a human decision the PM can't make)."""
        self._require_program(program_id)
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        target.demoted = demoted
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(
            f"program {program_id}: idea {idea_id} {'demoted' if demoted else 'un-demoted'}")
        return self._idea_public(target)

    def list_ideas(self, program_id: str) -> dict:
        self._require_program(program_id)
        summary, ideas = self.substrate.load_ideas(program_id)
        return {"summary": summary, "ideas": [self._idea_public(i) for i in ideas]}

    def add_idea(self, program_id: str, text: str, source: str = "human") -> dict:
        self._require_program(program_id)
        text = text.strip()
        if not text:
            raise ValueError("idea text is required")
        summary, ideas = self.substrate.load_ideas(program_id)
        idea = Idea(id=uuid4().hex[:8], text=text, source=source, created_at=time.time())
        ideas.append(idea)
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: idea added ({source})")
        return self._idea_public(idea)

    def delete_idea(self, program_id: str, idea_id: str, by: str = "human") -> None:
        self._require_program(program_id)
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        if by == "pm" and target.protected:
            raise ValueError("idea is protected; the PM may not delete it")
        ideas = [i for i in ideas if i.id != idea_id]
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: idea {idea_id} deleted ({by})")

    def set_idea_pin(self, program_id: str, idea_id: str, pinned: bool) -> dict:
        self._require_program(program_id)
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        target.pinned = pinned
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: idea {idea_id} {'pinned' if pinned else 'unpinned'}")
        return self._idea_public(target)

    def add_idea_comment(self, program_id: str, idea_id: str, text: str) -> dict:
        self._require_program(program_id)
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        target.comments.append({"id": uuid4().hex[:8], "text": text, "added_at": time.time()})
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: comment on idea {idea_id}")
        return self._idea_public(target)

    # --- results ---
    def list_results(self) -> list[dict]:
        return [{"id": r.id, "sprint": r.sprint, "summary": r.summary,
                 "completed_at": r.completed_at}
                for r in self.substrate.iter_results()]

    def get_result(self, result_id: str) -> dict:
        if not (self.repo_root / "results" / f"{result_id}.md").is_file():
            raise NotFoundError(result_id)
        r = self.substrate.load_result(result_id)
        program = None
        if (self.substrate.sprint_dir(r.sprint) / "sprint.md").is_file():
            program = self.substrate.load_sprint(r.sprint).program
        return {"id": r.id, "sprint": r.sprint, "summary": r.summary, "program": program,
                "completed_at": r.completed_at}

    # --- ledger ---
    def ledger_status(self) -> dict:
        ledger = self._ledger()
        return {
            "capacity": dict(self.pool.capacity),
            "used": ledger.used(),
            "available": ledger.available(),
            "leases": [
                {"id": l.id, "sprint_id": l.sprint_id, "amounts": l.amounts,
                 "granted_at": l.granted_at, "expires_at": l.expires_at,
                 "priority": l.priority, "preemptible": l.preemptible}
                for l in ledger.all_leases()
            ],
        }
