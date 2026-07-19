"""Transport-agnostic service API over the substrate + ledger.

Every method returns JSON-serialisable plain data so the MCP and HTTP layers
can hand results straight to clients.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

from coscience import graph, threads
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus, Program, ProgramStatus, Idea, ChatThread, set_status
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
        self._pool_override = pool     # tests may inject a fixed pool

    @property
    def pool(self) -> ResourcePool:
        # Read .coscience/resources.yaml live so capacity edits show without a server
        # restart (an injected pool, if any, wins — for tests). It's a tiny file.
        return self._pool_override if self._pool_override is not None else load_pool(self.repo_root)

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
                      artifacts_bound: list | None = None,
                      artifacts_create: list | None = None,
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
            artifacts_bound=[str(a) for a in (artifacts_bound or [])],
            artifacts_create=[dict(c) for c in (artifacts_create or [])],
        )
        self.substrate.save_sprint(sprint)
        return id

    def approve_sprint(self, sprint_id: str, by: str = "") -> None:
        """Human authorization: proposed -> approved. Cleared to run, but held
        until released with run_sprint (by you or the PM)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PROPOSED:
            raise ValueError(f"can only approve a proposed sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.APPROVED, by=by, action="approve")
        self.substrate.save_sprint(sprint)

    def run_sprint(self, sprint_id: str, by: str = "") -> None:
        """Release a sprint to the scheduler -> queued. Allowed from proposed (a
        one-step authorize+run) or approved; the dispatcher runs it as soon as a
        resource slot frees (it may wait in queue)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status not in (SprintStatus.PROPOSED, SprintStatus.APPROVED):
            raise ValueError(f"can only run a proposed or approved sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.QUEUED, by=by, action="run")
        self.substrate.save_sprint(sprint)

    def send_back_sprint(self, sprint_id: str, by: str = "") -> None:
        """Return an approved sprint to proposed for reconsideration."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.APPROVED:
            raise ValueError(f"can only send back an approved sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.PROPOSED, by=by, action="send_back")
        self.substrate.save_sprint(sprint)

    _REJECTABLE = (SprintStatus.PROPOSED, SprintStatus.APPROVED, SprintStatus.QUEUED)

    def reject_sprint(self, sprint_id: str, by: str = "") -> None:
        """Cancel a pre-execution sprint (proposed / approved / queued)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status not in self._REJECTABLE:
            raise ValueError(f"can only cancel a pre-run sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.CANCELED, by=by, action="reject")
        self.substrate.save_sprint(sprint)

    def park_sprint(self, sprint_id: str, by: str = "") -> None:
        """Human shelf: proposed -> parked. Frees a proposed-cap slot for the PM
        without deleting or demoting the sprint. Inert until unparked."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PROPOSED:
            raise ValueError(f"can only park a proposed sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.PARKED, by=by, action="park")
        self.substrate.save_sprint(sprint)

    def unpark_sprint(self, sprint_id: str, by: str = "") -> None:
        """Un-shelf: parked -> proposed (back into the review pool / PM cap)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PARKED:
            raise ValueError(f"can only unpark a parked sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.PROPOSED, by=by, action="unpark")
        self.substrate.save_sprint(sprint)

    def cancel_parked_sprint(self, sprint_id: str, by: str = "") -> None:
        """Cancel a parked sprint: parked -> canceled (record + git history stay;
        it just leaves the board)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.PARKED:
            raise ValueError(f"can only cancel a parked sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.CANCELED, by=by, action="cancel")
        self.substrate.save_sprint(sprint)

    def resume_sprint(self, sprint_id: str, by: str = "") -> None:
        """Manually re-open a finished/failed sprint for more work: drop its
        result(s), reset the retry/ambiguity counters, and re-queue it. The worker
        relaunches and the agent resumes from its scratchpad. For sprints wrongly
        marked done (e.g. the agent stopped without actually finishing)."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status not in (SprintStatus.DONE, SprintStatus.FAILED):
            raise ValueError(
                f"can only resume a done or failed sprint; {sprint_id} is {sprint.status.value}")
        for rid in list(sprint.results):
            self.substrate.delete_result(rid)
        sprint.results = []
        # Clear the completion sentinel so the fresh run must signal done anew.
        (self.substrate.sprint_dir(sprint_id) / "finished.json").unlink(missing_ok=True)
        progress = self.substrate.load_progress(sprint_id)
        progress.agent_token = ""
        progress.agent_session_id = ""      # don't --resume the prior finished session
        progress.failures = 0
        progress.ambiguous_exits = 0
        progress.scratch_size = 0
        progress.last_error = ""
        self.substrate.save_progress(progress)
        set_status(sprint, SprintStatus.QUEUED, by=by, action="resume")
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: resumed by {by or 'human'} (re-queued)")

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
                "last_status_at": self._last_status_at(sprint),
                "model": sprint.model,
                "activity": activity,
                "votes": self._vote_tally(sprint),
            })
        return rows

    def _last_status_at(self, sprint: Sprint) -> float:
        """Timestamp of the most recent status change. Uses the lifecycle
        timeline; falls back to creation time for legacy sprints with no
        recorded history."""
        if sprint.status_history:
            return float(sprint.status_history[-1]["at"])
        return self._appeared_at(sprint)

    def _activity(self, sprint_id: str) -> dict | None:
        from coscience.claude_executor import read_activity
        return read_activity(self.substrate.sprint_dir(sprint_id))

    def get_sprint(self, sprint_id: str, viewer: str = "") -> dict:
        sprint = self._load_sprint(sprint_id)
        progress = self.substrate.load_progress(sprint_id)
        lease = self._ledger().lease_for(sprint_id)
        if progress.job_token:
            agent_state = "sleeping"
        elif progress.agent_token:
            agent_state = "running"
        else:
            agent_state = "idle"
        job = None
        if progress.job_token:
            job = {"note": progress.job_note, "out_file": progress.job_out,
                   "started_at": progress.job_started_at,
                   "expected_seconds": progress.job_expected_seconds,
                   "next_wake": progress.job_next_wake,
                   "max_seconds": progress.job_max_seconds}
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
            "threads": [threads.public(t) for t in sprint.threads],
            "decisions": list(sprint.decisions),
            "status_history": list(sprint.status_history),
            "created_at": self._appeared_at(sprint),
            "votes": self._vote_tally(sprint, viewer),
            "agent_running": bool(progress.agent_token),
            "agent_state": agent_state,
            "job": job,
            "started_at": progress.started_at,
            "activity": self._activity(sprint_id) if sprint.status == SprintStatus.EXECUTING else None,
            "error": progress.last_error if sprint.status == SprintStatus.FAILED else "",
            "lease": None if lease is None else {
                "id": lease.id, "sprint_id": lease.sprint_id, "amounts": lease.amounts,
                "granted_at": lease.granted_at, "expires_at": lease.expires_at,
                "priority": lease.priority, "preemptible": lease.preemptible,
            },
        }

    def wake_sprint(self, sprint_id: str) -> dict:
        """Nudge a sleeping detached job to wake early: sets job_next_wake to now
        so the next worker beat assesses it, instead of waiting out its declared
        wake_after_seconds. A no-op (beyond the 404 check) if no job is tracked."""
        self._load_sprint(sprint_id)                 # 404 if missing
        progress = self.substrate.load_progress(sprint_id)
        if progress.job_token:
            progress.job_next_wake = time.time()
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint_id}: wake requested")
        return self.get_sprint(sprint_id)

    def usage_stats(self) -> dict:
        """Claude usage for the dashboard: the rolling 5h/weekly budget plus how
        many calls the PM and worker have each made (total / last hour / last day)."""
        from coscience import usage_meter
        return {"budget": usage_meter.read_budget(),
                "runs": usage_meter.run_stats(self.repo_root)}

    def add_sprint_comment(self, sprint_id: str, text: str, target: str = "worker",
                           by: str = "", thread_id: str = "") -> dict:
        """Start or continue a feedback thread on a sprint. Allowed in any
        status — it's feedback, not an edit. `target` routes a new thread:
        'worker' (the running agent reads it as direction) or 'pm' (the
        planner reads it and may revise the sprint or propose a follow-up).
        With `thread_id`, appends a human message to that thread instead
        (reopening it if it was marked complete)."""
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        if target not in ("worker", "pm"):
            raise ValueError("target must be 'worker' or 'pm'")
        sprint = self._load_sprint(sprint_id)
        if thread_id:
            t = next((x for x in sprint.threads if x["id"] == thread_id), None)
            if t is None:
                raise NotFoundError(thread_id)
            threads.append(t, "human", text, by, now=time.time())
        else:
            t = threads.new_thread(target, text, by, now=time.time())
            sprint.threads.append(t)
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: feedback ({target})")
        return threads.public(t)

    def complete_sprint_thread(self, sprint_id: str, thread_id: str) -> dict:
        return self._mutate_sprint_thread(sprint_id, thread_id, lambda t: t.update(status="complete"))

    def reopen_sprint_thread(self, sprint_id: str, thread_id: str) -> dict:
        return self._mutate_sprint_thread(sprint_id, thread_id, lambda t: t.update(status="open"))

    def seen_sprint_thread(self, sprint_id: str, thread_id: str) -> dict:
        return self._mutate_sprint_thread(sprint_id, thread_id, lambda t: t.update(agent_unseen=False))

    def _mutate_sprint_thread(self, sprint_id: str, thread_id: str, fn) -> dict:
        sprint = self._load_sprint(sprint_id)
        t = next((x for x in sprint.threads if x["id"] == thread_id), None)
        if t is None:
            raise NotFoundError(thread_id)
        fn(t)
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: thread {thread_id}")
        return threads.public(t)

    def delete_sprint_thread(self, sprint_id: str, thread_id: str) -> None:
        sprint = self._load_sprint(sprint_id)
        if not any(x["id"] == thread_id for x in sprint.threads):
            raise NotFoundError(thread_id)
        sprint.threads = [x for x in sprint.threads if x["id"] != thread_id]
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: thread {thread_id} deleted")

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
                         "last_status_at": self._last_status_at(s),
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

    def run_pm_directive(self, program_id: str, mode: str) -> dict:
        """Run one directed PM cycle now: 'compress' (merge/prune/re-rank the idea
        pool — only pinned ideas are spared) or 'brainstorm' (add fresh ideas).
        Same lock/usage path as replan; returns the beat summary."""
        if mode not in ("compress", "brainstorm"):
            raise ValueError(f"unknown pm directive: {mode!r}")
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        from coscience.pm_agent import pm_beat
        from coscience.pm_claude import ClaudeCodeReasoner
        from coscience.worker import claude_usage_ok
        return pm_beat(self.substrate, program_id, ClaudeCodeReasoner(),
                       usage_ok=claude_usage_ok, force=True, directive=mode)

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
    @staticmethod
    def _chat_public(thread: ChatThread, live: str = "") -> dict:
        return {"id": thread.id, "title": thread.title, "scope": thread.scope,
                "created_at": thread.created_at, "turns_done": thread.turns_done,
                "busy": thread.pending, "messages": list(thread.messages), "live": live}

    def _migrate_legacy_chat(self, program_id: str) -> None:
        """One-time: fold a pre-threads chat.md into a single imported thread."""
        if self.substrate.list_chat_threads(program_id):
            return
        old = self.substrate.load_chat(program_id)
        if not old:
            return
        t = ChatThread(id=uuid4().hex[:8], title="Imported chat", scope="read",
                       session_id=str(uuid4()), created_at=old[0].get("at", time.time()),
                       messages=old)
        self.substrate.save_chat_thread(program_id, t)
        (self.substrate.program_dir(program_id) / "chat.md").unlink(missing_ok=True)
        self.substrate.commit(f"program {program_id}: migrate chat to a thread")

    def _collect_if_ready(self, program_id: str, thread: ChatThread) -> ChatThread:
        """If a turn is in flight, collect it once its exit sentinel appears (append
        the PM reply, capture the session id, clear busy). Lazy — driven by polling."""
        if not thread.pending:
            return thread
        from coscience import chat_agent
        from coscience.executor import is_running
        tdir = self.substrate.chat_thread_dir(program_id, thread.id)
        text, sid, status = chat_agent.collect_turn(tdir)
        if status == "running":
            if thread.agent_token and not is_running(thread.agent_token):  # died, no exit
                thread.messages.append({"role": "pm", "at": time.time(),
                    "text": "_(The chat agent stopped before replying — send the message again.)_"})
                thread.pending, thread.agent_token = False, ""
                thread.messages = thread.messages[-200:]
                self.substrate.save_chat_thread(program_id, thread)
                self.substrate.commit(f"program {program_id}: chat {thread.id} interrupted")
            return thread
        reply = text if status == "ok" else (text or "_(The agent exited with an error.)_")
        thread.messages.append({"role": "pm", "text": reply, "at": time.time()})
        thread.pending, thread.agent_token = False, ""
        thread.turns_done += 1
        if sid:
            thread.session_id = sid
        thread.messages = thread.messages[-200:]
        self.substrate.save_chat_thread(program_id, thread)
        self.substrate.commit(f"program {program_id}: chat {thread.id} reply")
        return thread

    def list_chats(self, program_id: str) -> list[dict]:
        self._require_program(program_id)
        self._migrate_legacy_chat(program_id)
        out = []
        for t in self.substrate.list_chat_threads(program_id):
            t = self._collect_if_ready(program_id, t)
            out.append({"id": t.id, "title": t.title, "scope": t.scope,
                        "created_at": t.created_at, "busy": t.pending,
                        "messages": len(t.messages),
                        "last_at": t.messages[-1]["at"] if t.messages else t.created_at})
        return out

    def create_chat(self, program_id: str, title: str = "") -> dict:
        self._require_program(program_id)
        t = ChatThread(id=uuid4().hex[:8], title=(str(title).strip() or "New chat"),
                       scope="read", session_id=str(uuid4()), created_at=time.time())
        self.substrate.save_chat_thread(program_id, t)
        self.substrate.commit(f"program {program_id}: new chat {t.id}")
        return self._chat_public(t)

    def _thread_or_404(self, program_id: str, thread_id: str) -> ChatThread:
        self._require_program(program_id)
        t = self.substrate.load_chat_thread(program_id, thread_id)
        if t is None:
            raise NotFoundError(thread_id)
        return t

    def get_chat_thread(self, program_id: str, thread_id: str) -> dict:
        thread = self._collect_if_ready(program_id, self._thread_or_404(program_id, thread_id))
        live = ""
        if thread.pending:
            out = self.substrate.chat_thread_dir(program_id, thread_id) / "turn.out"
            live = out.read_text() if out.exists() else ""
        return self._chat_public(thread, live=live)

    def rename_chat(self, program_id: str, thread_id: str, title: str) -> dict:
        thread = self._thread_or_404(program_id, thread_id)
        title = str(title).strip()
        if not title:
            raise ValueError("title is required")
        thread.title = title[:120]
        self.substrate.save_chat_thread(program_id, thread)
        self.substrate.commit(f"program {program_id}: rename chat {thread_id}")
        return self._chat_public(thread)

    def set_chat_scope(self, program_id: str, thread_id: str, scope: str) -> dict:
        if scope not in ("read", "full"):
            raise ValueError("scope must be 'read' or 'full'")
        thread = self._thread_or_404(program_id, thread_id)
        thread.scope = scope
        self.substrate.save_chat_thread(program_id, thread)
        self.substrate.commit(f"program {program_id}: chat {thread_id} scope -> {scope}")
        return self._chat_public(thread)

    def delete_chat(self, program_id: str, thread_id: str) -> None:
        self._thread_or_404(program_id, thread_id)
        self.substrate.delete_chat_thread(program_id, thread_id)
        self.substrate.commit(f"program {program_id}: delete chat {thread_id}")

    def post_chat_message(self, program_id: str, thread_id: str, message: str,
                          by: str = "", launch=None) -> dict:
        """Append the human message and launch a detached, resumable chat turn in the
        program workdir. Returns immediately with busy=True; the reply is collected
        on a later poll. `launch(**kwargs)->token` is injectable for tests."""
        from coscience import chat_agent
        thread = self._thread_or_404(program_id, thread_id)
        message = str(message).strip()
        if not message:
            raise ValueError("message is required")
        if thread.pending:
            raise ValueError("this chat is still working on the previous message")
        thread.messages.append({"role": "user", "text": message, "at": time.time(),
                                "by": str(by or "")})
        from coscience.worker import claude_usage_ok
        if launch is None and not claude_usage_ok():
            thread.messages.append({"role": "pm", "at": time.time(),
                "text": "_(Claude usage is exhausted — please try again after the reset.)_"})
            thread.messages = thread.messages[-200:]
            self.substrate.save_chat_thread(program_id, thread)
            self.substrate.commit(f"program {program_id}: chat {thread_id} (usage paused)")
            return self._chat_public(thread)
        program = self.substrate.load_program(program_id)
        workdir = chat_agent.resolve_workdir(self.substrate, program.workdir)
        resume = thread.turns_done > 0
        if resume:
            # The preamble (with the TOOLS/scope line) went out only on turn 1. If the
            # scope changed since it was last announced, tell the resumed session now —
            # else it keeps acting on its original scope (e.g. thinks it's still read-only).
            if thread.scope != thread.announced_scope:
                prompt = chat_agent.scope_change_notice(thread.scope) + "\n\nHuman: " + message
            else:
                prompt = message
        else:
            from coscience.pm_agent import gather_context
            ctx = gather_context(self.substrate, program_id)
            prompt = chat_agent.render_preamble(ctx, thread.scope) + "\n\nHuman: " + message
        thread.announced_scope = thread.scope
        launch = launch or chat_agent.launch_turn
        token = launch(thread_dir=self.substrate.chat_thread_dir(program_id, thread_id),
                       workdir=workdir, prompt=prompt, scope=thread.scope,
                       session_id=thread.session_id, resume=resume, model=program.pm_model)
        thread.pending, thread.agent_token = True, str(token)
        thread.messages = thread.messages[-200:]
        self.substrate.save_chat_thread(program_id, thread)
        self.substrate.commit(f"program {program_id}: chat {thread_id} message")
        return self._chat_public(thread)

    def list_guidance(self, program_id: str) -> list[dict]:
        self._require_program(program_id)
        return [threads.public(t) for t in self.substrate.load_guidance(program_id)]

    def add_guidance(self, program_id: str, text: str, by: str = "", thread_id: str = "") -> dict:
        """Start or continue a standing-guidance feedback thread for the PM. Guidance
        threads always target the PM. With `thread_id`, appends a human message to
        that thread instead of starting a new one (reopening it if it was complete)."""
        text = text.strip()
        if not text:
            raise ValueError("guidance text is required")
        self._require_program(program_id)
        guidance_threads = self.substrate.load_guidance(program_id)
        if thread_id:
            t = next((x for x in guidance_threads if x["id"] == thread_id), None)
            if t is None:
                raise NotFoundError(thread_id)
            threads.append(t, "human", text, by, now=time.time())
        else:
            t = threads.new_thread("pm", text, by, now=time.time())
            guidance_threads.append(t)
        self.substrate.save_guidance(program_id, guidance_threads)
        self.substrate.commit(f"program {program_id}: guidance added")
        return threads.public(t)

    def remove_guidance(self, program_id: str, thread_id: str) -> None:
        self._require_program(program_id)
        guidance_threads = [t for t in self.substrate.load_guidance(program_id) if t["id"] != thread_id]
        self.substrate.save_guidance(program_id, guidance_threads)

    def complete_guidance_thread(self, program_id: str, thread_id: str) -> dict:
        return self._mutate_guidance_thread(program_id, thread_id,
                                            lambda t: t.update(status="complete"))

    def reopen_guidance_thread(self, program_id: str, thread_id: str) -> dict:
        return self._mutate_guidance_thread(program_id, thread_id,
                                            lambda t: t.update(status="open"))

    def seen_guidance_thread(self, program_id: str, thread_id: str) -> dict:
        return self._mutate_guidance_thread(program_id, thread_id,
                                            lambda t: t.update(agent_unseen=False))

    def _mutate_guidance_thread(self, program_id: str, thread_id: str, fn) -> dict:
        self._require_program(program_id)
        guidance_threads = self.substrate.load_guidance(program_id)
        t = next((x for x in guidance_threads if x["id"] == thread_id), None)
        if t is None:
            raise NotFoundError(thread_id)
        fn(t)
        self.substrate.save_guidance(program_id, guidance_threads)
        self.substrate.commit(f"program {program_id}: guidance thread {thread_id}")
        return threads.public(t)

    # --- ideas ---
    @staticmethod
    def _idea_public(i: Idea) -> dict:
        return {"id": i.id, "text": i.text, "source": i.source, "by": i.by,
                "pinned": i.pinned, "protected": i.protected,
                "threads": [threads.public(t) for t in i.threads],
                "created_at": i.created_at, "demoted": i.demoted}

    def demote_sprint(self, sprint_id: str, by: str = "") -> dict:
        """Demote a proposed/approved sprint into a non-promotable idea. The idea
        is flagged 'demoted' (the PM may not promote it back); a human can lift that.
        The sprint is canceled so it leaves the board."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status not in (SprintStatus.PROPOSED, SprintStatus.APPROVED, SprintStatus.PARKED):
            raise ValueError(
                f"only a proposed, approved, or parked sprint can be demoted (is {sprint.status.value})")
        if not sprint.program:
            raise ValueError("sprint has no program to hold the idea")
        summary, ideas = self.substrate.load_ideas(sprint.program)
        text = (sprint.title or sprint.goals or sprint.id).strip()
        idea = Idea(id=uuid4().hex[:8], text=text, source="human",
                    demoted=True, pinned=True, created_at=time.time())   # demote auto-pins
        ideas.append(idea)
        # Rewire the sprint's graph edges onto the new idea. Drop evidential edges
        # incident on the sprint first (an idea has no result to confirm/refute),
        # then repoint the rest across every program idea + sprint. Use the live
        # `sprint` object in the node set (NOT an iter_sprints copy) so its own
        # edges are drained on the object we save last.
        program_sprints = [s for s in self.substrate.iter_sprints()
                           if s.program == sprint.program and s.id != sprint.id]
        nodes = list(ideas) + program_sprints + [sprint]
        changed = graph.drop_evidential_incident(sprint.id, nodes)
        changed |= graph.repoint_edges(sprint.id, idea.id, nodes)
        # Repoint preserves edge type, so an experiment->experiment lineage edge can
        # degrade to an illegal kind pair once it lands on the new idea; drop those
        # ("repoint … where still valid", spec §4.2).
        changed |= graph.drop_kind_illegal_incident(idea.id, nodes)
        sprint_by_id = {s.id: s for s in program_sprints}
        for nid in changed:
            if nid in sprint_by_id:
                self.substrate.save_sprint(sprint_by_id[nid])
        self.substrate.save_ideas(sprint.program, summary, ideas)
        set_status(sprint, SprintStatus.CANCELED, by=by, action="demote")
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
        if demoted:
            target.pinned = True             # demote auto-pins (pinned == protected)
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(
            f"program {program_id}: idea {idea_id} {'demoted' if demoted else 'un-demoted'}")
        return self._idea_public(target)

    def list_ideas(self, program_id: str) -> dict:
        self._require_program(program_id)
        summary, ideas = self.substrate.load_ideas(program_id)
        return {"summary": summary, "ideas": [self._idea_public(i) for i in ideas]}

    # --- lineage graph ---
    def _program_nodes(self, program_id: str):
        """(ideas, sprints) for a program — the live node set the graph spans."""
        _summary, ideas = self.substrate.load_ideas(program_id)
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        return ideas, sprints

    def _save_node(self, program_id: str, node, ideas) -> None:
        """Persist one node after an edge change: a sprint saves directly; an idea
        requires re-saving the whole pool (single ideas.md)."""
        if isinstance(node, Sprint):
            self.substrate.save_sprint(node)
        else:
            summary, _ = self.substrate.load_ideas(program_id)
            self.substrate.save_ideas(program_id, summary, ideas)

    def add_edge(self, program_id: str, etype: str, src: str, dst: str, by: str = "",
                 rationale: str = "", confidence: str = "", evidence: str = "") -> dict:
        self._require_program(program_id)
        ideas, sprints = self._program_nodes(program_id)
        nodes = list(ideas) + sprints
        node_by_id = {n.id: n for n in nodes}
        edge = graph.new_edge(etype, src, dst, "human", by=by, at=time.time(),
                              rationale=rationale, confidence=confidence, evidence=evidence)
        reason = graph.validate_edge(edge, nodes, graph.all_edges(nodes))
        if reason is not None:
            raise ValueError(reason)
        if any(e["id"] == edge["id"] for e in node_by_id[src].edges):
            raise ValueError("edge already exists")
        node_by_id[src].edges.append(edge)
        self._save_node(program_id, node_by_id[src], ideas)
        self.substrate.commit(f"program {program_id}: add edge {edge['id']} ({etype})")
        return edge

    def delete_edge(self, program_id: str, edge_id: str) -> dict:
        self._require_program(program_id)
        ideas, sprints = self._program_nodes(program_id)
        for n in list(ideas) + sprints:
            kept = [e for e in n.edges if e["id"] != edge_id]
            if len(kept) != len(n.edges):
                n.edges = kept
                self._save_node(program_id, n, ideas)
                self.substrate.commit(f"program {program_id}: delete edge {edge_id}")
                return {"deleted": edge_id}
        raise NotFoundError(edge_id)

    def get_graph(self, program_id: str) -> dict:
        self._require_program(program_id)
        ideas, sprints = self._program_nodes(program_id)
        # Canceled sprints are off the graph (a demote already rewired their edges).
        sprints = [s for s in sprints if s.status != SprintStatus.CANCELED]
        live = list(ideas) + sprints
        live_ids = {n.id for n in live}
        nodes = [{"id": i.id, "kind": graph.node_kind(i), "stage": graph.node_stage(i),
                  "label": i.text[:80], "status": ""} for i in ideas]
        nodes += [{"id": s.id, "kind": graph.node_kind(s), "stage": graph.node_stage(s),
                   "label": (s.title or s.goals)[:80], "status": s.status.value} for s in sprints]
        # Drop any edge that touches an excluded (canceled) node, so nothing dangles.
        edges = [e for e in graph.all_edges(live)
                 if e["src"] in live_ids and e["dst"] in live_ids]
        return {"nodes": nodes, "edges": edges}

    def add_idea(self, program_id: str, text: str, source: str = "human", by: str = "") -> dict:
        self._require_program(program_id)
        text = text.strip()
        if not text:
            raise ValueError("idea text is required")
        summary, ideas = self.substrate.load_ideas(program_id)
        # A human-authored idea is auto-pinned (pinned == protected). PM-authored
        # ideas start unpinned and prunable.
        idea = Idea(id=uuid4().hex[:8], text=text, source=source, created_at=time.time(),
                    by=str(by or ""), pinned=(source == "human"))
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
        # Cascade: drop every edge pointing AT the deleted idea so no surviving node
        # is left with a dangling reference (mirrors the PM prune path).
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        changed = graph.drop_edges_to(idea_id, list(ideas) + sprints)
        sprint_by_id = {s.id: s for s in sprints}
        for nid in changed:
            if nid in sprint_by_id:
                self.substrate.save_sprint(sprint_by_id[nid])
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

    def add_idea_comment(self, program_id: str, idea_id: str, text: str, by: str = "",
                         thread_id: str = "") -> dict:
        """Start or continue a feedback thread on an idea. Idea threads always
        target the PM — there's no worker running against a pool idea. With
        `thread_id`, appends a human message to that thread instead of starting
        a new one (reopening it if it was marked complete)."""
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        self._require_program(program_id)
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        if thread_id:
            t = next((x for x in target.threads if x["id"] == thread_id), None)
            if t is None:
                raise NotFoundError(thread_id)
            threads.append(t, "human", text, by, now=time.time())
        else:
            t = threads.new_thread("pm", text, by, now=time.time())
            target.threads.append(t)
        target.pinned = True                 # a human comment auto-pins (pinned == protected)
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: comment on idea {idea_id}")
        return threads.public(t)

    def complete_idea_thread(self, program_id: str, idea_id: str, thread_id: str) -> dict:
        return self._mutate_idea_thread(program_id, idea_id, thread_id,
                                        lambda t: t.update(status="complete"))

    def reopen_idea_thread(self, program_id: str, idea_id: str, thread_id: str) -> dict:
        return self._mutate_idea_thread(program_id, idea_id, thread_id,
                                        lambda t: t.update(status="open"))

    def seen_idea_thread(self, program_id: str, idea_id: str, thread_id: str) -> dict:
        return self._mutate_idea_thread(program_id, idea_id, thread_id,
                                        lambda t: t.update(agent_unseen=False))

    def _mutate_idea_thread(self, program_id: str, idea_id: str, thread_id: str, fn) -> dict:
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        t = next((x for x in target.threads if x["id"] == thread_id), None)
        if t is None:
            raise NotFoundError(thread_id)
        fn(t)
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: idea {idea_id} thread {thread_id}")
        return threads.public(t)

    def delete_idea_thread(self, program_id: str, idea_id: str, thread_id: str) -> None:
        summary, ideas = self.substrate.load_ideas(program_id)
        target = next((i for i in ideas if i.id == idea_id), None)
        if target is None:
            raise NotFoundError(idea_id)
        if not any(x["id"] == thread_id for x in target.threads):
            raise NotFoundError(thread_id)
        target.threads = [x for x in target.threads if x["id"] != thread_id]
        self.substrate.save_ideas(program_id, summary, ideas)
        self.substrate.commit(f"program {program_id}: idea {idea_id} thread {thread_id} deleted")

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

    # --- artifacts ---
    def _artifact_sprints(self, program_id: str, aid: str) -> list[dict]:
        from coscience import artifacts
        out = []
        for s in self.substrate.iter_sprints():
            if s.program == program_id and aid in artifacts.sprint_aids(s):
                out.append({"id": s.id, "status": s.status.value, "title": s.title})
        return out

    def _artifact_version_files(self, program_id: str, aid: str, vid: str) -> list[str]:
        vdir = self.substrate.artifact_dir(program_id, aid) / vid
        if not vdir.is_dir():
            return []
        return sorted(str(p.relative_to(vdir)) for p in vdir.rglob("*") if p.is_file())

    def list_artifacts(self, program_id: str) -> list[dict]:
        out = []
        for a in self.substrate.iter_artifacts(program_id):
            out.append({
                "id": a.id, "title": a.title, "kind": a.kind, "current": a.current,
                "archived": a.archived, "lock": a.lock,
                "version_count": sum(1 for v in a.versions if not v.archived),
                "linked_sprints": self._artifact_sprints(program_id, a.id),
            })
        return out

    def get_artifact(self, program_id: str, aid: str) -> dict:
        from coscience import threads as _th
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        a = self.substrate.load_artifact(program_id, aid)
        return {
            "id": a.id, "program": program_id, "title": a.title, "kind": a.kind,
            "current": a.current, "archived": a.archived, "lock": a.lock,
            "versions": [
                {"id": v.id, "parent": v.parent, "created_at": v.created_at,
                 "created_by": v.created_by, "archived": v.archived, "note": v.note}
                for v in a.versions],
            "threads": [_th.public(t) for t in a.threads],
            "current_files": self._artifact_version_files(program_id, aid, a.current) if a.current else [],
            "linked_sprints": self._artifact_sprints(program_id, aid),
        }

    def artifact_version_dir(self, program_id: str, aid: str, vid: str) -> Path:
        try:
            base = self.substrate.artifact_dir(program_id, aid).resolve()
            root = (self.substrate.repo_root / "programs").resolve()
            if not base.is_relative_to(root):
                raise NotFoundError(vid)          # program_id/aid escaped the substrate
            d = (base / vid).resolve()
        except (ValueError, OSError):
            raise NotFoundError(vid)
        if d.parent != base or not d.is_dir():
            raise NotFoundError(vid)
        return d

    def _guarded_file(self, program_id: str, aid: str, vid: str, relpath: str) -> Path:
        vdir = self.artifact_version_dir(program_id, aid, vid)
        try:
            path = (vdir / relpath).resolve()
        except (ValueError, OSError):
            raise NotFoundError(relpath)
        if not path.is_file() or not path.is_relative_to(vdir):
            raise NotFoundError(relpath)
        return path

    def read_artifact_file(self, program_id: str, aid: str, vid: str, name: str) -> dict:
        path = self._guarded_file(program_id, aid, vid, name)
        raw = path.read_bytes()
        binary = b"\x00" in raw[:8192]
        return {"name": name, "size": len(raw),
                "content": "" if binary else raw.decode("utf-8", errors="replace"),
                "binary": binary}

    def artifact_page_file(self, program_id: str, aid: str, vid: str, relpath: str) -> Path:
        return self._guarded_file(program_id, aid, vid, relpath)

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
