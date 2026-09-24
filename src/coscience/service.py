"""Transport-agnostic service API over the substrate + ledger.

Every method returns JSON-serialisable plain data so the MCP and HTTP layers
can hand results straight to clients.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from uuid import uuid4

import yaml

from coscience import commit_health, disk, graph, host_health, host_removal, threads
from coscience.artifacts import DESCRIPTION_FILE, FIGURE_DESCRIPTION_NOTE
from coscience.ledger import Ledger
from coscience.models import (DEFAULT_MODEL, Sprint, SprintStatus, Program, ProgramStatus,
                              Idea, ChatThread, set_status, status_actor)
from coscience.pause import is_paused
from coscience.resources import (GPU_KEY, GPU_VRAM_KEY, LOCAL, PLATFORM_KEYS,
                                 ResourcePool, _parse_programs, _parse_host, load_pool,
                                 pool_file_hosts, pool_file_lock, write_pool_file)
from coscience.substrate import Substrate, check_host_name


def service_from_env() -> "Service":
    """Construct a Service from COSCIENCE_REPO (default: current directory)."""
    repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))
    return Service(repo_root)


def _clean_card(card: dict) -> dict:
    """One card as written to the pool file: numbers as floats, `disabled` only when set."""
    out = {**card, "vram_gb": float(card["vram_gb"])}
    if out.get("total_vram_gb") is not None:
        out["total_vram_gb"] = float(out["total_vram_gb"])
    else:
        out.pop("total_vram_gb", None)
    if not out.get("disabled"):
        out.pop("disabled", None)
    return out


class NoteChanged(ValueError):
    """A server note was saved over a version its editor never saw (O22). Carries the
    note as it now stands, so the page can show it instead of losing either side."""

    def __init__(self, host: str, current: str):
        super().__init__(f"the note on {host} changed since you opened it")
        self.host = host
        self.current = current


class NotFoundError(KeyError):
    """A requested sprint or result does not exist."""


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
# Never worth quoting on an overview card, even though some are technically text.
_OPAQUE_SUFFIXES = {".pyc", ".pyo", ".so", ".o", ".bin", ".zip", ".gz", ".tar",
                    ".npy", ".npz", ".h5", ".hdf5", ".pkl", ".parquet", ".pdf"}


def _is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_SUFFIXES


LABEL_MAX = 60


def _clean_label(value) -> str:
    """A server's display name: one line of ordinary text, or "" to go by the name.
    It is never an identity — nothing is keyed on it — so the only rules are that it
    fits on a card and cannot smuggle in line breaks."""
    text = " ".join(str(value or "").split())
    if len(text) > LABEL_MAX:
        raise ValueError(f"a display name is at most {LABEL_MAX} characters")
    return text


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
                      preemptible: bool = True, distributed: bool = False,
                      resources_required: dict | None = None,
                      artifacts_bound: list | None = None,
                      artifacts_create: list | None = None,
                      status: str = "proposed", from_idea: str = "",
                      title: str = "", summary: str = "", rationale: str = "",
                      by: str = "") -> str:
        if not plan:
            raise ValueError("plan must have at least one suggested step")
        if (self.substrate.sprint_dir(id) / "sprint.md").is_file():
            raise ValueError(f"sprint {id} already exists")
        if from_idea:
            # Checked before anything is written: a promotion of an idea that is not
            # in the pool must not leave a sprint behind.
            _summary, pool = self.substrate.load_ideas(program) if program else ("", [])
            if not any(i.id == from_idea for i in pool):
                raise ValueError(f"idea {from_idea} is not in {program or 'any'} program's pool")
        sprint = Sprint(
            id=id,
            status=SprintStatus(status),
            goals=goals,
            plan=[str(step) for step in plan],
            program=program,
            resources_required={k: float(v) for k, v in (resources_required or {}).items()},
            priority=priority,
            preemptible=preemptible,
            distributed=bool(distributed),
            artifacts_bound=[str(a) for a in (artifacts_bound or [])],
            artifacts_create=[dict(c) for c in (artifacts_create or [])],
            model=self._worker_default(program),
            title=str(title or ""), summary=str(summary or ""), rationale=str(rationale or ""),
        )
        # Record who wrote it, so the program page can tell a proposal the viewer made
        # from one the PM brought them (P3). save_sprint only backfills a history entry
        # when there is none, so this is the one that survives.
        set_status(sprint, sprint.status, by=by, action="propose")
        self.substrate.save_sprint(sprint)
        if from_idea:
            self._promote_idea(program, from_idea, id)
        return id

    def _promote_idea(self, program_id: str, idea_id: str, sprint_id: str) -> None:
        """A human's promotion, ending the way the PM's does: the idea's edges (both
        directions) move onto the sprint it became, and it leaves the pool — the
        sprint now carries the lineage, so keeping the idea would read as a duplicate."""
        summary, ideas = self.substrate.load_ideas(program_id)
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        changed = graph.repoint_edges(idea_id, sprint_id, list(ideas) + sprints)
        sprint_by_id = {s.id: s for s in sprints}
        for nid in changed:
            if nid in sprint_by_id:
                self.substrate.save_sprint(sprint_by_id[nid])
        self.substrate.save_ideas(program_id, summary, [i for i in ideas if i.id != idea_id])
        self.substrate.commit(f"program {program_id}: idea {idea_id} promoted to sprint {sprint_id}")

    def draft_sprint_from_idea(self, program_id: str, idea_id: str, drafter=None) -> dict:
        """The planner drafts a proposal from a pool idea — the fields a PM proposal
        carries — for a human to review in the proposal form. Nothing is written but
        the call's row: submitting the form is still what creates the sprint."""
        self._require_program(program_id)
        _summary, ideas = self.substrate.load_ideas(program_id)
        idea = next((i for i in ideas if i.id == idea_id), None)
        if idea is None:
            raise NotFoundError(idea_id)
        from coscience import usage_meter
        from coscience.executor import process_token
        from coscience.pm_agent import gather_context
        from coscience.pm_claude import draft_sprint
        ctx = gather_context(self.substrate, program_id)
        # The call runs inside this server process, so that process is its token.
        call = usage_meter.start_call(self.substrate.repo_root, "pm-draft", program=program_id,
                                      model=ctx.model, limits=usage_meter.current_window(),
                                      token=process_token(os.getpid()))
        try:
            draft, env = (drafter or draft_sprint)(ctx, idea.text)
        except Exception:
            usage_meter.finish_call(self.substrate.repo_root, call, status="failed", model=ctx.model)
            raise
        before, after = env.get("limits") or (None, None)
        usage_meter.finish_call(self.substrate.repo_root, call, status="ok", model=ctx.model,
                                cost=env.get("total_cost_usd"), turns=env.get("num_turns"),
                                limits=after, limits_before=before)
        suffix = "".join(ch if ch.isalnum() else "-" for ch in str(draft.pop("suffix", "")).lower())
        suffix = "-".join(part for part in suffix.split("-") if part) or f"idea-{idea_id}"
        return {"id": f"{program_id}-{suffix}", **draft}

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
        sprint.hold = {}        # running it answers whatever the planner held it for
        self.substrate.save_sprint(sprint)

    def clear_sprint_hold(self, sprint_id: str, by: str = "") -> None:
        """Lift the planner's hold, leaving the sprint approved and releasable. The
        human's override of a "not yet": the sprint does not move, so this is not a
        lifecycle transition and writes no status_history entry."""
        sprint = self._load_sprint(sprint_id)
        if not sprint.hold:
            raise ValueError(f"{sprint_id} is not held")
        sprint.hold = {}
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: hold cleared by {by or 'human'}")

    def send_back_sprint(self, sprint_id: str, by: str = "") -> None:
        """Return an approved sprint to proposed for reconsideration."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.APPROVED:
            raise ValueError(f"can only send back an approved sprint; {sprint_id} is {sprint.status.value}")
        set_status(sprint, SprintStatus.PROPOSED, by=by, action="send_back")
        sprint.hold = {}        # it is no longer approved, so there is nothing to hold
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
        self._clear_for_relaunch(sprint_id)
        set_status(sprint, SprintStatus.QUEUED, by=by, action="resume")
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: resumed by {by or 'human'} (re-queued)")

    def _clear_for_relaunch(self, sprint_id: str) -> None:
        """Wipe everything a past life left in progress, so a re-queued sprint starts
        as a fresh run. Shared by resume (done/failed) and restore (canceled)."""
        # Clear the completion sentinel so the fresh run must signal done anew.
        (self.substrate.sprint_dir(sprint_id) / "finished.json").unlink(missing_ok=True)
        progress = self.substrate.load_progress(sprint_id)
        progress.agent_token = ""
        progress.agent_session_id = ""      # don't --resume the prior finished session
        progress.failures = 0
        progress.beat_failures = 0
        progress.ambiguous_exits = 0
        progress.scratch_size = 0
        progress.last_error = ""
        # A prior life's escalation state must not leak into this one: a sprint the
        # PM once answered, later finished and re-queued, would otherwise send its
        # next (unrelated) escalation straight to a human.
        progress.pm_answered = False
        progress.escalation = {}
        progress.resume_note = ""
        progress.reallocate_to = ""
        # A sprint canceled by a human stop still carries the stop that killed it.
        # Left set, the very next beat would stop the restored sprint again.
        progress.stop_requested = False
        self.substrate.save_progress(progress)

    # Where a restore puts a sprint back, by the status it was canceled from. The
    # pre-run statuses go back untouched; anything that was live when it was canceled
    # is re-queued, because its agent is gone and its lease released — there is no
    # "executing" left to return to, only a fresh run.
    _RESTORE_TO = {
        SprintStatus.PROPOSED.value: SprintStatus.PROPOSED,
        SprintStatus.APPROVED.value: SprintStatus.APPROVED,
        SprintStatus.QUEUED.value: SprintStatus.QUEUED,
        SprintStatus.PARKED.value: SprintStatus.PARKED,
        SprintStatus.EXECUTING.value: SprintStatus.QUEUED,
        SprintStatus.ESCALATED.value: SprintStatus.QUEUED,
        SprintStatus.HIBERNATED.value: SprintStatus.QUEUED,
    }

    def restore_sprint(self, sprint_id: str, by: str = "") -> str:
        """Undo a cancel: put a canceled sprint back where it was canceled from, and
        return the status it landed in. A human decision only — the PM reaches neither
        this nor any other route out of `canceled`.

        Cancel was the one human action with no way back, so a misclick cost the whole
        record: goals, plan, threads, votes and lineage. The exception is a demoted
        sprint, whose life continued as an idea; restoring it would leave both."""
        sprint = self._load_sprint(sprint_id)
        if sprint.status != SprintStatus.CANCELED:
            raise ValueError(
                f"can only restore a canceled sprint; {sprint_id} is {sprint.status.value}")
        history = sprint.status_history
        cancel_at = max((i for i, e in enumerate(history)
                         if e.get("status") == SprintStatus.CANCELED.value), default=-1)
        if cancel_at >= 0 and str(history[cancel_at].get("action") or "") == "demote":
            raise ValueError(
                f"{sprint_id} was demoted to an idea, and the idea is where its life "
                "continued; restoring it would leave both. Promote the idea instead.")
        was = str(history[cancel_at - 1].get("status") or "") if cancel_at > 0 else ""
        target = self._RESTORE_TO.get(was, SprintStatus.PROPOSED)
        if target == SprintStatus.QUEUED:
            self._clear_for_relaunch(sprint_id)
        else:
            # Even a pre-run cancel can carry a pending stop (a human stop races the
            # beat that cancels), and nothing else would ever clear it.
            progress = self.substrate.load_progress(sprint_id)
            if progress.stop_requested:
                progress.stop_requested = False
                self.substrate.save_progress(progress)
        set_status(sprint, target, by=by, action="restore")
        self.substrate.save_sprint(sprint)
        self.substrate.commit(
            f"sprint {sprint_id}: restored by {by or 'human'} to {target.value}")
        return target.value

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
                    resources_required=None, preemptible=None, distributed=None,
                    model=None, title=None, summary=None, rationale=None) -> None:
        """A human's edit (P1). Goals, plan and the rationale are the proposal itself,
        so they change only while it is proposed — after approval they are what was
        approved. The title and summary only name and describe the work, so they stay
        editable until it is done or canceled, like the scheduler knobs and the model."""
        sprint = self._load_sprint(sprint_id)
        st = sprint.status
        if st in (SprintStatus.DONE, SprintStatus.CANCELED):
            raise ValueError(f"{sprint_id} is {st.value} and is read-only")
        if (goals is not None or plan is not None) and st != SprintStatus.PROPOSED:
            raise ValueError("goals/plan are editable only while proposed")
        if rationale is not None and st != SprintStatus.PROPOSED:
            raise ValueError("the rationale is editable only while proposed")
        if title is not None:
            sprint.title = str(title).strip()
        if summary is not None:
            sprint.summary = str(summary).strip()
        if rationale is not None:
            sprint.rationale = str(rationale).strip()
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
        if distributed is not None:
            sprint.distributed = bool(distributed)
        if model is not None and str(model or self._worker_default(sprint.program)) != sprint.model:
            # The model is switchable at any time. A detached agent can't change model
            # mid-process, so if one is already running we stop it; the next dispatch
            # beat relaunches on the new model and resumes from the scratchpad.
            sprint.model = str(model or self._worker_default(sprint.program))
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
        pool = self._ledger().pool
        rows = []
        for sprint in self.substrate.iter_sprints(status=wanted):
            started = None
            activity = None
            escalation_level = ""
            if sprint.status == SprintStatus.EXECUTING:
                started = self.substrate.load_progress(sprint.id).started_at
                activity = self._activity(sprint.id)
            elif sprint.status == SprintStatus.ESCALATED:
                escalation_level = str(
                    (self.substrate.load_progress(sprint.id).escalation or {}).get("level") or "")
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
                "distributed": sprint.distributed,
                "unrunnable": self._unrunnable(sprint, pool),
                "started_at": started,
                "last_status_at": self._last_status_at(sprint),
                "last_status_by": self._last_status_by(sprint),
                "hold": dict(sprint.hold),
                "model": sprint.model,
                "activity": activity,
                "escalation_level": escalation_level,
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

    def _last_status_by(self, sprint: Sprint) -> str:
        """Who made the most recent status change: "human", "pm" or "platform".
        The dashboard highlights what it did not ask for, so it needs the actor
        beside the time (P3). A sprint with no history was never moved by anyone."""
        if not sprint.status_history:
            return "platform"
        return status_actor(sprint.status_history[-1])

    def _activity(self, sprint_id: str) -> dict | None:
        from coscience.claude_executor import read_activity
        return read_activity(self.substrate.sprint_dir(sprint_id))

    def pulse_status(self) -> dict:
        """What the rail's pulse shows about compute (O23): each machine's free space and
        whether the substrate is committing. The rail is on every page, so every open tab
        polls this — it reads the pool file and the health file, never a sprint, where
        `ledger_status` parses every sprint on the box."""
        health = host_health.load(self.repo_root)
        return {
            "hosts": [{"name": h.name, "label": h.label, **self._disk(h, health)}
                      for h in self.pool.hosts],
            "commit_error": commit_health.describe(commit_health.read(self.substrate.repo_root)),
        }

    def _disk(self, host, health: dict) -> dict:
        """`free_gb` and `disk` ("", "low" or "critical") for one host. A machine that
        has not reported a reading carries free_gb None and disk "" — unknown never
        warns and never gates."""
        free = (disk.free_gb(self.repo_root) if host.is_local
                else health.get(host.name, {}).get("free_gb"))
        free = float(free) if isinstance(free, (int, float)) else None
        return {"free_gb": free, "disk": disk.level(free)}

    def _create_specs(self, sprint: Sprint) -> list[dict]:
        """The sprint's create-targets, each said whether it exists yet and at which
        version. The spec itself is never rewritten once the artifact is made, so
        without this a finished sprint still reads "will be created" and offers no
        way to the thing it made."""
        out = []
        for spec in sprint.artifacts_create:
            c = dict(spec)
            aid = str(c.get("aid") or "").strip()
            c["exists"], c["version"] = False, ""
            if sprint.program and aid:
                try:
                    art = self.substrate.load_artifact(sprint.program, aid)
                except (OSError, ValueError, yaml.YAMLError):
                    pass          # never made, or unreadable: it reads as a promise
                else:
                    c["exists"], c["version"] = True, art.current
            out.append(c)
        return out

    def _unrunnable(self, sprint: Sprint, pool) -> str:
        """Why this sprint can never be granted, or "". Only for sprints still headed
        for a grant: a finished one's request no longer matters."""
        if sprint.status in (SprintStatus.DONE, SprintStatus.CANCELED, SprintStatus.FAILED):
            return ""
        from coscience.resources import describe_over_capacity, over_capacity_on
        progress = self.substrate.load_progress(sprint.id)
        # A pending `reallocate` answer is where the next grant will pin this
        # sprint (the dispatcher's grant step pins on `reallocate_to or host` too),
        # even though nothing has landed there yet — check that target, not just
        # where its work already is (fix round 1, I1).
        pinned = progress.reallocate_to or progress.host
        if pinned:
            host = pool.host(pinned)
            if host is None or not host.placeable:
                return f"its work is on host {pinned}, which is not in the pool or not taking work"
            if host.removing:
                return (f"{pinned} is being removed and this sprint's work is there: "
                        "stop the sprint, or keep the server")
            if host.drain:
                return (f"{pinned} is draining and this sprint is pinned there: "
                        "keep the server or stop the sprint")
            entry = host_health.load(self.repo_root).get(pinned)
            if host_health.state(entry, time.time()) == "quiet":
                fail_since = float((entry or {}).get("fail_since") or 0.0)
                return (f"pinned to {pinned}, which has not answered since "
                        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(fail_since))}: "
                        "its work is kept and it takes no new grants")
            if not host.allows(sprint.program):
                # The host is fine in general — its `programs:` restriction changed
                # (or was added) after this sprint's work landed there.
                return f"its work is on host {pinned}, which no longer takes work for this program"
        closest, over = over_capacity_on(sprint.resources_required, pool, sprint.program,
                                         only_host=pinned or None)
        text = describe_over_capacity(over)
        if text and closest and not pinned and len(pool.placeable_hosts(sprint.program)) > 1:
            text += f" (closest host: {closest})"
        return text

    def get_sprint(self, sprint_id: str, viewer: str = "") -> dict:
        sprint = self._load_sprint(sprint_id)
        progress = self.substrate.load_progress(sprint_id)
        ledger = self._ledger()
        lease = ledger.lease_for(sprint_id)
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
        escalation = None
        if sprint.status == SprintStatus.ESCALATED and progress.escalation:
            from coscience import escalation as escalation_mod
            esc = progress.escalation
            hosts_allowed = escalation_mod.move_targets(
                self.pool, sprint.program, progress.host, self.repo_root)
            escalation = {"level": esc.get("level", "pm"), "by": esc.get("by", ""),
                         "at": esc.get("at", 0.0), "host": esc.get("host", ""),
                         "what": esc.get("what", ""), "tried": esc.get("tried", ""),
                         "may_have_broken_something": bool(esc.get("may_have_broken_something", False)),
                         "needs": esc.get("needs", ""), "thread_id": esc.get("thread_id", ""),
                         "hosts_allowed": hosts_allowed,
                         "stop_requested": bool(progress.stop_requested)}
        return {
            "id": sprint.id,
            "status": sprint.status.value,
            "title": sprint.title,
            "summary": sprint.summary,
            "goals": sprint.goals,
            "priority": sprint.priority,
            "preemptible": sprint.preemptible,
            "resources_required": sprint.resources_required,
            "distributed": sprint.distributed,
            "unrunnable": self._unrunnable(sprint, ledger.pool),
            "rationale": sprint.rationale,
            "program": sprint.program,
            "model": sprint.model,
            "results": list(sprint.results),
            "plan": list(sprint.plan),
            "artifacts_bound": list(sprint.artifacts_bound),
            "artifacts_create": self._create_specs(sprint),
            "hold": dict(sprint.hold),
            "pm_notes": [dict(n) for n in sprint.pm_notes],
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
            # A canceled sprint carries a note too: a human stop says what it ended and,
            # when it could not reach a job on its host, says that instead of implying a
            # clean stop (O18).
            "error": progress.last_error if (sprint.status in (SprintStatus.FAILED,
                                                               SprintStatus.CANCELED)
                                             or progress.last_error.startswith("beat failed:")) else "",
            "escalation": escalation,
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

    _STOPPABLE = (SprintStatus.EXECUTING, SprintStatus.HIBERNATED, SprintStatus.ESCALATED)
    _FINISHED = (SprintStatus.DONE, SprintStatus.FAILED, SprintStatus.CANCELED)

    def stop_sprint(self, sprint_id: str, by: str = "") -> dict:
        """A human stops running work. Only records the request — sets the same
        `progress.stop_requested` flag escalation.answer's own "stop" action sets —
        the dispatcher carries it out (Worker.run_sprint_beat / run_escalated_beat),
        exactly as it does for a stop that followed an escalation; the web process
        must never kill agents itself."""
        sprint = self._load_sprint(sprint_id)                 # NotFoundError if missing
        if sprint.status in self._STOPPABLE:
            progress = self.substrate.load_progress(sprint_id)
            progress.stop_requested = True
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint_id}: stop requested")
            return self.get_sprint(sprint_id)
        if sprint.status in self._FINISHED:
            raise ValueError(f"{sprint_id} has already finished")
        raise ValueError(f"{sprint_id} is not running yet; cancel it instead")

    def answer_escalation(self, sprint_id: str, action: str, *, instructions: str = "",
                          host: str = "", by: str = "", thread_id: str = "") -> dict:
        """A human answers an escalated sprint (dashboard/HTTP path only — the PM
        answers through its own reasoner loop, not this method). A literal `by`
        of "pm" is not trusted as the PM: it is remapped so it can never act with
        PM authority (e.g. answer a PM-level escalation, or skip a human-only
        action's guard)."""
        self._load_sprint(sprint_id)                 # NotFoundError if missing
        from coscience import escalation
        effective_by = by or "human"
        if effective_by == "pm":
            effective_by = "human:pm"
        why = escalation.answer(self.substrate, sprint_id, action, instructions=instructions,
                                host=host, by=effective_by, pool=self.pool, thread_id=thread_id)
        if why:
            raise ValueError(why)
        self.substrate.commit(f"sprint {sprint_id}: escalation answered ({action}) by {effective_by}")
        return self.get_sprint(sprint_id)

    def attention(self) -> dict:
        """Escalations currently with a human — the dashboard's cross-program
        to-do list. PM-level escalations don't need a human yet, so they're left out."""
        rows = []
        for sprint in self.substrate.iter_sprints(status=SprintStatus.ESCALATED):
            esc = self.substrate.load_progress(sprint.id).escalation or {}
            if esc.get("level") == "human":
                rows.append({"sprint_id": sprint.id, "program": sprint.program,
                            "title": sprint.title, "what": esc.get("what", ""),
                            "at": esc.get("at", 0.0)})
        return {"escalated_to_human": rows}

    def usage_stats(self) -> dict:
        """Claude usage for the dashboard: the rolling 5h/weekly budget plus how
        many calls the PM and worker have each made (total / last hour / last day)."""
        from coscience import usage_meter
        return {"budget": usage_meter.read_budget(),
                "runs": usage_meter.run_stats(self.repo_root)}

    def call_log(self, limit: int = 200) -> dict:
        """Every Claude call this host has made for this substrate, newest first —
        what the Compute log renders. Bounded, because the log grows one row per
        call forever and a table cannot render an unbounded history."""
        from coscience import usage_meter
        return {"calls": usage_meter.recent_calls(self.repo_root, limit=limit)}

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

    def sprint_file_path(self, sprint_id: str, name: str) -> Path:
        """Guarded resolution of one file in a sprint directory: no traversal, no
        hidden/internal files, must sit directly in the sprint dir. Shared by the
        JSON reader and the raw-bytes route (so a figure the agent dropped next to
        its scratchpad can be shown instead of flagged as binary)."""
        self._load_sprint(sprint_id)  # raises NotFoundError for unknown sprints
        d = self.substrate.sprint_dir(sprint_id).resolve()
        try:
            path = (d / name).resolve()
        except (ValueError, OSError):
            raise NotFoundError(name)
        if (path.parent != d or not path.is_file()
                or path.name.startswith(".") or path.name in self._DOC_HIDDEN):
            raise NotFoundError(name)
        return path

    def read_sprint_file(self, sprint_id: str, name: str) -> dict:
        """Full (untruncated) content of one sprint document — backs the UI's
        'show full log' toggle, where list_sprint_files tails large files."""
        path = self.sprint_file_path(sprint_id, name)
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

    def create_program(self, title: str, goals: str, workdir: str = "",
                       hosts: list[str] | None = None) -> dict:
        """Create a program from the dashboard. Title and goals are required
        (blank after strip -> ValueError); the id is assigned server-side via
        next_program_id(). Returns the same dict shape as get_program.

        `hosts`, when given, restricts which servers may run it: every server
        not named ends up with the new program excluded from its list (an absent
        list is reified to every program id first). `None` means every server,
        and no access write happens — but the program file is still written
        under `pool_file_lock` (see below), even on this path.

        The program-file write always happens under one `pool_file_lock` hold,
        together with the access write when `hosts` is given (fix round 1,
        Finding 3; widened again in fix round 2, New Issue 1 — the first pass
        only widened the `hosts is not None` branch, leaving the *more* common
        `hosts=None` path exposed to exactly the same race): `save_program`
        makes the new program visible to `iter_programs()`, and any concurrent,
        unrelated `set_program_hosts`/`create_program` call that reifies an
        absent-list server could take its `iter_programs()` snapshot before
        that write lands if it weren't serialized against this one — baking a
        list that omits the brand-new program forever, since nothing else ever
        revisits an absent-list server on an existing program's behalf. One
        `pool_file_lock` acquisition covers both writes on every path, and only
        `_set_program_hosts_locked` (never `set_program_hosts`, which takes the
        lock itself) is called from inside it, so there is still exactly one
        `fcntl.flock` acquisition per call."""
        title = str(title or "").strip()
        goals = str(goals or "").strip()
        if not title:
            raise ValueError("title is required")
        if not goals:
            raise ValueError("goals is required")
        program = Program(id=self.substrate.next_program_id(), title=title,
                          goals=goals, workdir=str(workdir or "").strip())
        changed = False
        with pool_file_lock(self.repo_root):
            self.substrate.save_program(program)
            if hosts is not None:
                changed = self._set_program_hosts_locked(program.id, hosts)
        if changed:
            self.substrate.commit(f"program {program.id} server access updated")
        return self.get_program(program.id)

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
            "wiki_model": p.wiki_model, "wiki_enabled": p.wiki_enabled,
            "wiki_merge": p.wiki_merge, "chat_model": p.chat_model,
            "worker_model": p.worker_model,
            "max_proposed": p.max_proposed,
            "instructions": self.substrate.load_instructions(program_id),
            "report": self.substrate.load_report(program_id),
            # Which earlier cycles' reports are still readable (E2). The current one is
            # above; these are the ones the next cycle would otherwise have erased.
            "report_cycles": self.substrate.report_cycles(program_id),
            "cycle": pm.cycle,
            "activations": list(reversed(pm.activations)),   # newest first, for the timeline
            "last_run": pm.last_run,
            "sprints": [{"id": s.id, "status": s.status.value, "goals": s.goals,
                         "title": s.title, "results": list(s.results), "model": s.model,
                         "last_status_at": self._last_status_at(s),
                         "last_status_by": self._last_status_by(s),
                         "hold": dict(s.hold),
                         "escalation_level": (
                             str((self.substrate.load_progress(s.id).escalation or {}).get("level") or "")
                             if s.status == SprintStatus.ESCALATED else ""),
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
        """Set the Claude model the PM reasoner uses for this program ("" = DEFAULT_MODEL)."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.pm_model = str(model or DEFAULT_MODEL)
        self.substrate.save_program(program)
        return {"id": program_id, "pm_model": program.pm_model}

    def set_program_wiki_model(self, program_id: str, model: str) -> dict:
        """Set the Claude model this program's wiki runs use ("" = DEFAULT_MODEL).

        Deliberately separate from `pm_model`: planning is a reasoning job over the
        program's state, wiki ingest is a reading-and-writing job over its results,
        and the model that is best value for one is not the model that is best value
        for the other."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.wiki_model = str(model or DEFAULT_MODEL)
        self.substrate.save_program(program)
        return {"id": program_id, "wiki_model": program.wiki_model}

    def set_program_chat_model(self, program_id: str, model: str) -> dict:
        """Set the Claude model this program's chat turns run on ("" = its pm_model).

        Separate from `pm_model` so tuning autonomous planning does not also change
        what a human is talking to — chat is the one agent someone waits on."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.chat_model = str(model or program.pm_model)
        self.substrate.save_program(program)
        return {"id": program_id, "chat_model": program.chat_model}

    def set_program_worker_model(self, program_id: str, model: str) -> dict:
        """Set the model this program's NEW sprints inherit ("" = DEFAULT_MODEL).

        Applied when a sprint is proposed, never to sprints that already exist: those
        were reviewed with a model attached, and moving them under a human who has
        already looked at them would be a surprise."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.worker_model = str(model or DEFAULT_MODEL)
        self.substrate.save_program(program)
        return {"id": program_id, "worker_model": program.worker_model}

    def _worker_default(self, program_id: str | None) -> str:
        """The model a sprint of `program_id` runs on when it names none."""
        if program_id and (self.substrate.program_dir(program_id) / "program.md").is_file():
            return self.substrate.load_program(program_id).worker_model
        return DEFAULT_MODEL

    def set_program_wiki_enabled(self, program_id: str, enabled: bool) -> dict:
        """Opt a program in or out of wiki ingest entirely. False makes the wiki
        beat skip it, so no run is ever launched and no quota is spent on it."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.wiki_enabled = bool(enabled)
        self.substrate.save_program(program)
        return {"id": program_id, "wiki_enabled": program.wiki_enabled}

    def set_program_max_proposed(self, program_id: str, n: int) -> dict:
        """Cap how many sprints may await review for this program. 0 clears the
        override, putting the program back on the global default."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        n = int(n)
        if n < 0 or n > 20:
            raise ValueError("max_proposed must be between 0 and 20 (0 = default)")
        program = self.substrate.load_program(program_id)
        program.max_proposed = n
        self.substrate.save_program(program)
        return {"id": program_id, "max_proposed": program.max_proposed}

    def _paused_beat(self, program_id: str) -> dict:
        """The skip result for a human-triggered PM beat refused by the global pause.
        Shaped like pm_beat's own throttle skip, but flagged `paused` rather than
        `throttled`: both stop the beat, only one is cleared by a usage reset. Told
        apart here so the UI can say "Resume in Compute" instead of sending the human
        off to wait for a reset that will change nothing."""
        return {"program": program_id,
                "cycle": self.substrate.load_pm_state(program_id).cycle,
                "submitted": [], "proposed": [], "skipped": True, "paused": True}

    def _pm_reasoner(self):
        """A reasoner that records its transcript, same as the loop's. Human-triggered
        beats cost exactly what a loop beat costs, so they leave the same trace."""
        from coscience.pm_claude import ClaudeCodeReasoner
        return ClaudeCodeReasoner(transcript_dir=self.substrate.repo_root / ".coscience")

    def replan(self, program_id: str) -> dict:
        """Run one PM cycle for this program right now (forced) so a human edit or
        comment is acted on without waiting for the loop tick. The per-program lock
        inside pm_beat keeps this from racing the background loop; returns the beat
        summary (with `busy` if the loop was mid-cycle)."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        from coscience.pm_agent import pm_beat
        from coscience.worker import claude_usage_ok
        if is_paused(self.substrate.repo_root):
            return self._paused_beat(program_id)
        return pm_beat(self.substrate, program_id, self._pm_reasoner(),
                       usage_ok=lambda: claude_usage_ok(
                           repo_root=self.substrate.repo_root), force=True)

    def run_pm_directive(self, program_id: str, mode: str) -> dict:
        """Run one directed PM cycle now: 'compress' (merge/prune/re-rank the idea
        pool — only pinned ideas are spared) or 'brainstorm' (add fresh ideas).
        Same lock/usage path as replan; returns the beat summary."""
        if mode not in ("compress", "brainstorm"):
            raise ValueError(f"unknown pm directive: {mode!r}")
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        from coscience.pm_agent import pm_beat
        from coscience.worker import claude_usage_ok
        if is_paused(self.substrate.repo_root):
            return self._paused_beat(program_id)
        return pm_beat(self.substrate, program_id, self._pm_reasoner(),
                       usage_ok=lambda: claude_usage_ok(
                           repo_root=self.substrate.repo_root),
                       force=True, directive=mode)

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

    def set_program_goals(self, program_id: str, goals: str) -> dict:
        goals = str(goals or "").strip()
        if not goals:
            raise ValueError("goals is required")
        self._require_program(program_id)
        program = self.substrate.load_program(program_id)
        program.goals = goals
        self.substrate.save_program(program)
        self.substrate.commit(f"program {program_id}: goals updated")
        return self.get_program(program_id)

    def set_program_instructions(self, program_id: str, text: str) -> dict:
        """Replace this program's standing house rules for the PM. They land in every
        PM prompt from the next cycle on, and the edit itself wakes the PM (the
        fingerprint covers them), so a policy change is acted on without waiting for
        unrelated activity."""
        self._require_program(program_id)
        self.substrate.save_instructions(program_id, str(text or ""))
        self.substrate.commit(f"program {program_id}: instructions updated")
        return self.get_program(program_id)

    def get_host_notes(self, program_id: str) -> dict:
        """This program's own note per server, plus the reports workers filed that the
        PM has not folded in yet (O9)."""
        self._require_program(program_id)
        # The servers, and which this program may use, are all the notes card needs to
        # know about compute (O23). It used to poll the whole ledger for them, and the
        # ledger parses every sprint on the box to answer.
        return {"notes": self.substrate.list_host_notes(program_id),
                "reports": self.substrate.load_host_reports(program_id),
                "hosts": [{"name": h.name, "label": h.label, "allowed": h.allows(program_id)}
                          for h in self.pool.hosts]}

    def set_host_note(self, program_id: str, host: str, text: str,
                      report_ids: list[str] | None = None, base: str | None = None) -> dict:
        """Replace one server's note by hand. Saving clears that server's pending
        reports: a human who read them and wrote the note has folded them in, and
        nothing else should show them to the PM again.

        `report_ids` names the reports the page actually showed. A sprint can file one
        between the page loading and Save landing, and that one has been read by nobody
        — it stays pending. An older dashboard sends nothing and clears the server, as
        it always did.

        `base` is the note as the editor first saw it. When the note has changed since —
        another person, or a planner cycle, saved in between — nothing is written and
        `NoteChanged` says so: two editors used to overwrite each other silently (O22).
        None skips the check, for a client that sends no base.

        A note can only be started on a server this program may use. One that already
        holds a note or reports stays editable after access is withdrawn — that is the
        only way to clear it — but a mistyped server name cannot create one (O22)."""
        self._require_program(program_id)
        check_host_name(host)
        notes = self.substrate.list_host_notes(program_id)
        h = self.pool.host(host)
        if (h is None or not h.allows(program_id)) and host not in notes and not any(
                r.get("host") == host for r in self.substrate.load_host_reports(program_id)):
            raise ValueError(f"program {program_id} may not use {host}, so it keeps no note there")
        if base is not None and notes.get(host, "") != str(base).strip():
            raise NoteChanged(host, notes.get(host, ""))
        self.substrate.save_host_note(program_id, host, str(text or ""))
        self.substrate.clear_host_reports(program_id, host, ids=report_ids)
        self.substrate.commit(f"program {program_id}: notes on {host} updated")
        return self.get_host_notes(program_id)

    def _require_program(self, program_id: str) -> None:
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)

    # --- PM chat (ask the planner clarifying questions; answer-only) ---
    @staticmethod
    def _chat_public(thread: ChatThread, live: str = "") -> dict:
        return {"id": thread.id, "title": thread.title, "scope": thread.scope,
                "created_at": thread.created_at, "turns_done": thread.turns_done,
                "busy": thread.pending, "messages": list(thread.messages), "live": live,
                "artifacts": list(thread.artifacts)}

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
        the PM reply, capture the session id, clear busy). On a read; the dispatcher
        also collects every pending thread each cycle."""
        from coscience import chat_agent
        return chat_agent.collect_thread(self.substrate, program_id, thread)

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

    def create_chat(self, program_id: str, title: str = "",
                    artifacts: list | None = None) -> dict:
        from coscience import artifacts as _art
        self._require_program(program_id)
        aids = [str(a) for a in (artifacts or [])]
        tid = uuid4().hex[:8]
        t = ChatThread(id=tid, title=(str(title).strip() or "New chat"),
                       scope="full" if aids else "read",
                       session_id=str(uuid4()), created_at=time.time(),
                       artifacts=aids)
        if aids:
            for aid in aids:
                if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
                    raise ValueError(f"artifact not found: {aid}")
            ok = _art.acquire_lock(self.substrate, program_id, aids, "chat",
                                   f"chat:{tid}", time.time())
            if not ok:
                raise ValueError("artifact busy — held by another editor")
        self.substrate.save_chat_thread(program_id, t)
        self.substrate.commit(f"program {program_id}: new chat {t.id}"
                              + (f" bound to {aids}" if aids else ""))
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

    def save_chat_version(self, program_id: str, thread_id: str) -> dict:
        from coscience import artifacts as _art
        thread = self._thread_or_404(program_id, thread_id)
        if not thread.artifacts:
            raise ValueError("this chat is not bound to an artifact")
        out: dict = {}
        for aid in thread.artifacts:
            vid = _art.cut_version_for(self.substrate, program_id, aid,
                                       f"chat:{thread_id}", time.time())
            out[aid] = vid
        self.substrate.commit(f"program {program_id}: chat {thread_id} saved versions {out}")
        return out

    def delete_chat(self, program_id: str, thread_id: str) -> None:
        from coscience import artifacts as _art
        thread = self._thread_or_404(program_id, thread_id)
        if thread.artifacts:
            _art.release_lock(self.substrate, program_id, list(thread.artifacts),
                              time.time(), created_by=f"chat:{thread_id}")
        self.substrate.delete_chat_thread(program_id, thread_id)
        self.substrate.commit(f"program {program_id}: delete chat {thread_id}")

    def release_chat(self, program_id: str, thread_id: str) -> dict:
        """Finish an artifact-editing session: cut a final version of anything
        changed since the last save, release the lock, and unbind the artifact(s)
        so the chat won't silently re-lock on its next message. The chat thread and
        its history stay; the artifact becomes free and re-bindable. Returns the
        updated (now-unbound) thread plus {aid: version_id | None} for what was cut."""
        from coscience import artifacts as _art
        thread = self._thread_or_404(program_id, thread_id)
        if not thread.artifacts:
            raise ValueError("this chat is not bound to an artifact")
        if thread.pending:
            raise ValueError("this chat is still working — wait for the turn to finish, then release")
        aids = list(thread.artifacts)
        # release_lock cuts a final version (dedup-aware) and clears work/ + the lock.
        vids = _art.release_lock(self.substrate, program_id, aids,
                                 time.time(), created_by=f"chat:{thread_id}")
        saved = dict(zip(aids, vids))
        thread.artifacts = []
        self.substrate.save_chat_thread(program_id, thread)
        self.substrate.commit(f"program {program_id}: chat {thread_id} released {aids}")
        return {"thread": self._chat_public(thread), "saved": saved}

    def post_chat_message(self, program_id: str, thread_id: str, message: str,
                          by: str = "", launch=None) -> dict:
        """Append the human message and launch a detached, resumable chat turn in the
        program workdir (or the bound artifact's work/ dir). Returns immediately with busy=True; the reply is collected
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
        # Pause first: it also fails the usage gate below, but a reset does not clear
        # it — Resume does — so the human must be told which stop this is.
        if launch is None and is_paused(self.substrate.repo_root):
            thread.messages.append({"role": "pm", "at": time.time(),
                "text": "_(Paused — Resume in Compute to keep talking.)_"})
            thread.messages = thread.messages[-200:]
            self.substrate.save_chat_thread(program_id, thread)
            self.substrate.commit(f"program {program_id}: chat {thread_id} (paused)")
            return self._chat_public(thread)
        if launch is None and not claude_usage_ok(repo_root=self.substrate.repo_root):
            thread.messages.append({"role": "pm", "at": time.time(),
                "text": "_(Claude usage is exhausted — please try again after the reset.)_"})
            thread.messages = thread.messages[-200:]
            self.substrate.save_chat_thread(program_id, thread)
            self.substrate.commit(f"program {program_id}: chat {thread_id} (usage paused)")
            return self._chat_public(thread)
        program = self.substrate.load_program(program_id)
        workdir = chat_agent.resolve_workdir(self.substrate, program.workdir)
        if thread.artifacts:
            from coscience import artifacts as _art
            # Re-acquire in case a prior idle session's lock was reaped (which rmtree'd
            # work/). Same-holder acquire is a no-op keeping work/; a reaped lock re-locks
            # and re-seeds work/ from current; a lock now held by someone else is rejected.
            if not _art.acquire_lock(self.substrate, program_id, list(thread.artifacts),
                                     "chat", f"chat:{thread_id}", time.time()):
                raise ValueError("artifact busy — held by another editor")
            aid0 = thread.artifacts[0]
            workdir = str(self.substrate.artifact_dir(program_id, aid0) / "work")
            for aid in thread.artifacts:
                _art.bump_activity(self.substrate, program_id, aid, time.time())
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
        if thread.artifacts:
            figure_note = ""
            if any(self._artifact_kind(program_id, a) == "figure" for a in thread.artifacts):
                figure_note = " " + FIGURE_DESCRIPTION_NOTE
            prompt = (f"[ARTIFACT] You are editing artifact(s) {thread.artifacts} — your working "
                      f"directory IS the artifact's working copy. Create and edit files here; "
                      f"the human snapshots them as versions.{figure_note}\n\n") + prompt
        thread.announced_scope = thread.scope
        launch = launch or chat_agent.launch_turn
        token = launch(thread_dir=self.substrate.chat_thread_dir(program_id, thread_id),
                       workdir=workdir, prompt=prompt, scope=thread.scope,
                       session_id=thread.session_id, resume=resume, model=program.chat_model)
        from coscience import usage_meter
        thread.pending, thread.agent_token = True, str(token)
        # A guidance/chat turn is a Claude call like any other and was the one
        # spender that reached the ledger not at all.
        thread.agent_call = usage_meter.start_call(
            self.substrate.repo_root, "chat", program=program_id,
            model=program.chat_model, limits=usage_meter.current_window(), token=str(token))
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

    _THUMB_CHARS = 240          # enough for an overview card, not a second copy of the doc

    def list_artifacts(self, program_id: str, include_archived: bool = False) -> list[dict]:
        out = []
        for a in self.substrate.iter_artifacts(program_id, include_archived=include_archived):
            files = self._artifact_version_files(program_id, a.id, a.current) if a.current else []
            out.append({
                "id": a.id, "title": a.title, "kind": a.kind, "current": a.current,
                "archived": a.archived, "lock": a.lock,
                "version_count": sum(1 for v in a.versions if not v.archived),
                "tags": list(a.tags),
                # Enough for the overview to draw a thumbnail without a request per
                # card: the file names (the caller picks the image) and, for text
                # kinds, the opening of the document.
                "files": files,
                "excerpt": self._artifact_excerpt(program_id, a, files),
            })
        return out

    def _artifact_excerpt(self, program_id: str, art, files: list[str]) -> str:
        """The opening of an artifact's current text file, for overview thumbnails.
        A figure's only quotable file is its description.md, so that is its sole
        candidate. Other kinds try candidates in turn rather than trusting the first
        name: a code artifact's alphabetically-first file is often a build leftover
        (a .pyc under __pycache__), which would leave the card blank."""
        if not files or not art.current:
            return ""
        candidates = ([f for f in files if f == DESCRIPTION_FILE] if art.kind == "figure"
                      else self._excerpt_candidates(files))
        for name in candidates:
            try:
                raw = self._guarded_file(program_id, art.id, art.current,
                                         name).read_bytes()[:self._THUMB_CHARS * 4]
            except (NotFoundError, OSError):
                continue
            if b"\x00" in raw:
                continue                          # compiled/binary: try the next file
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                return text[:self._THUMB_CHARS]
        return ""

    @staticmethod
    def _excerpt_candidates(files: list[str]) -> list[str]:
        """Readable files, prose first — a README says more about an artifact on a
        card than whichever source file happens to sort first."""
        usable = [f for f in files
                  if not _is_image_name(f)
                  and "__pycache__" not in f
                  and Path(f).suffix.lower() not in _OPAQUE_SUFFIXES]
        prose = [f for f in usable if Path(f).suffix.lower() in {".md", ".txt", ".rst"}]
        return prose + [f for f in usable if f not in prose]

    def adopt_artifact(self, program_id: str, aid: str, title: str = "", kind: str = "md",
                       files: list | None = None, content: str = "", filename: str = "",
                       note: str = "", by: str = "") -> dict:
        """Register output that already exists as an artifact (new, or a new version
        of an existing one) and snapshot it — no sprint, no compute grant. `files`
        resolve against the program's workdir, then the substrate root so a finished
        sprint's own output (`sprints/<id>/figure.png`) is reachable, and may not
        escape either that workdir or this program's sprints — the same rule the PM's
        own adoption follows."""
        from coscience import artifacts
        from coscience.pm_agent import _resolve_workdir
        self._require_program(program_id)
        base = _resolve_workdir(self.substrate, self.substrate.load_program(program_id).workdir)
        own_sprint_dirs = [self.substrate.sprint_dir(sp.id)
                           for sp in self.substrate.iter_sprints() if sp.program == program_id]
        sources = artifacts.resolve_sources(
            [base, self.substrate.repo_root], [str(f) for f in (files or [])],
            roots=[base, *own_sprint_dirs])
        vid = artifacts.adopt(self.substrate, program_id, aid, title=title, kind=kind,
                              now=time.time(), created_by=by or "human",
                              sources=sources, content=content, filename=filename,
                              note=note)
        self.substrate.commit(f"artifact {program_id}/{aid}: adopted {vid or '(no change)'}")
        return self.get_artifact(program_id, aid)

    def _artifact_kind(self, program_id: str, aid: str) -> str:
        """The artifact's kind, or "" when it doesn't exist yet — a chat can be bound
        to an id whose artifact the agent has still to create."""
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            return ""
        return self.substrate.load_artifact(program_id, aid).kind

    def get_artifact(self, program_id: str, aid: str) -> dict:
        from coscience import threads as _th
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        a = self.substrate.load_artifact(program_id, aid)
        return {
            "id": a.id, "program": program_id, "title": a.title, "kind": a.kind,
            "current": a.current, "archived": a.archived, "lock": a.lock, "tags": list(a.tags),
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

    def artifact_version_file_path(self, program_id: str, aid: str, vid: str, name: str) -> Path:
        """Guarded path to one file inside a committed version, for the raw-bytes
        route. The /download route can't serve a figure whose version also holds its
        generator script — it zips anything multi-file — so images address the file."""
        return self._guarded_file(program_id, aid, vid, name)

    def read_artifact_file(self, program_id: str, aid: str, vid: str, name: str) -> dict:
        path = self._guarded_file(program_id, aid, vid, name)
        raw = path.read_bytes()
        binary = b"\x00" in raw[:8192]
        return {"name": name, "size": len(raw),
                "content": "" if binary else raw.decode("utf-8", errors="replace"),
                "binary": binary}

    def artifact_work_file_path(self, program_id: str, aid: str, name: str) -> Path:
        """Guarded resolution of a file inside an artifact's live work/ dir. Confined
        to work/ (no traversal, must stay inside the substrate). Used by both the
        JSON reader and the raw-bytes route (so images can be shown live)."""
        work = (self.substrate.artifact_dir(program_id, aid) / "work").resolve()
        root = (self.substrate.repo_root / "programs").resolve()
        if not work.is_relative_to(root) or not work.is_dir():
            raise NotFoundError(name)
        try:
            path = (work / name).resolve()
        except (ValueError, OSError):
            raise NotFoundError(name)
        if not path.is_file() or not path.is_relative_to(work):
            raise NotFoundError(name)
        return path

    def read_artifact_work_file(self, program_id: str, aid: str, name: str) -> dict:
        path = self.artifact_work_file_path(program_id, aid, name)
        raw = path.read_bytes()
        binary = b"\x00" in raw[:8192]
        return {"name": name, "size": len(raw),
                "content": "" if binary else raw.decode("utf-8", errors="replace"),
                "binary": binary}

    def artifact_page_file(self, program_id: str, aid: str, vid: str, relpath: str) -> Path:
        return self._guarded_file(program_id, aid, vid, relpath)

    def revert_artifact(self, program_id: str, aid: str, vid: str) -> dict:
        from coscience import artifacts
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        artifacts.revert(self.substrate, program_id, aid, vid)   # ValueError on unknown vid
        self.substrate.commit(f"artifact {program_id}/{aid}: revert to {vid}")
        return self.get_artifact(program_id, aid)

    def set_artifact_archived(self, program_id: str, aid: str, archived: bool) -> dict:
        from coscience import artifacts
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        artifacts.archive_artifact(self.substrate, program_id, aid, archived)
        self.substrate.commit(f"artifact {program_id}/{aid}: archived={archived}")
        return self.get_artifact(program_id, aid)

    def set_artifact_version_archived(self, program_id: str, aid: str, vid: str,
                                      archived: bool) -> dict:
        from coscience import artifacts
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        artifacts.archive_version(self.substrate, program_id, aid, vid, archived)
        self.substrate.commit(f"artifact {program_id}/{aid}: version {vid} archived={archived}")
        return self.get_artifact(program_id, aid)

    def set_artifact_tags(self, program_id: str, aid: str, tags: list[str]) -> dict:
        self._require_program(program_id)
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        a = self.substrate.load_artifact(program_id, aid)
        a.tags = sorted(set(t.strip() for t in tags if t.strip()))
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: tags={a.tags}")
        return self.get_artifact(program_id, aid)

    def list_artifact_tags(self, program_id: str) -> list[str]:
        self._require_program(program_id)
        tags: set[str] = set()
        for a in self.substrate.iter_artifacts(program_id, include_archived=True):
            tags.update(a.tags)
        return sorted(tags)

    def _load_artifact(self, program_id: str, aid: str):
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        return self.substrate.load_artifact(program_id, aid)

    def add_artifact_comment(self, program_id: str, aid: str, text: str,
                             by: str = "", thread_id: str = "") -> dict:
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        a = self._load_artifact(program_id, aid)
        if thread_id:
            t = next((x for x in a.threads if x["id"] == thread_id), None)
            if t is None:
                raise NotFoundError(thread_id)
            threads.append(t, "human", text, by, now=time.time())
        else:
            t = threads.new_thread("pm", text, by, now=time.time())   # artifact threads -> PM
            a.threads.append(t)
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: comment")
        return threads.public(t)

    def _mutate_artifact_thread(self, program_id: str, aid: str, thread_id: str, fn) -> dict:
        a = self._load_artifact(program_id, aid)
        t = next((x for x in a.threads if x["id"] == thread_id), None)
        if t is None:
            raise NotFoundError(thread_id)
        fn(t)
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: thread {thread_id}")
        return threads.public(t)

    def complete_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> dict:
        return self._mutate_artifact_thread(program_id, aid, thread_id,
                                            lambda t: t.update(status="complete"))

    def reopen_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> dict:
        return self._mutate_artifact_thread(program_id, aid, thread_id,
                                            lambda t: t.update(status="open"))

    def seen_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> dict:
        return self._mutate_artifact_thread(program_id, aid, thread_id,
                                            lambda t: t.update(agent_unseen=False))

    def delete_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> None:
        a = self._load_artifact(program_id, aid)
        if not any(x["id"] == thread_id for x in a.threads):
            raise NotFoundError(thread_id)
        a.threads = [x for x in a.threads if x["id"] != thread_id]
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: thread {thread_id} deleted")

    def list_artifact_work_files(self, program_id: str, aid: str) -> list[str]:
        work = self.substrate.artifact_dir(program_id, aid) / "work"
        if not work.is_dir():
            return []
        return sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file())

    # --- ledger ---
    def _leftover_by_host(self, pool, health: dict[str, dict]) -> dict[str, list[dict]]:
        """{host name: [{"sprint_id", "status", "path"}]} — the run directories a host
        ACTUALLY holds, as named by its last health check, labelled from the sprint
        records (O20).

        The sprint records say what a host was asked to run, not what is still on its
        disk: an agent that tidies up after itself used to be listed anyway, which sent
        people to delete folders that were already gone. So the listing decides what is
        there and the records only explain it. A folder a live sprint is using is not a
        leftover; a folder no record explains is listed as "unknown", which is the case
        the old list could never show. A host with no listing on record — never checked,
        or checked by an older build — contributes nothing: an empty listing means the
        run root is empty, a missing one means nobody has looked, and guessing is what
        this replaced."""
        listed = {h.name: (h, health.get(h.name, {}).get("run_dirs"))
                  for h in pool.hosts if h.ssh and h.run_root}
        listed = {name: (h, dirs) for name, (h, dirs) in listed.items()
                  if isinstance(dirs, list) and dirs}
        if not listed:
            return {}
        wanted = {name for _, dirs in listed.values() for name in dirs}
        # One walk, and only for the ids actually on a disk somewhere.
        status_of = {s.id: s.status for s in self.substrate.iter_sprints() if s.id in wanted}
        finished = (SprintStatus.DONE, SprintStatus.CANCELED, SprintStatus.FAILED)
        out: dict[str, list[dict]] = {}
        for name, (host, dirs) in listed.items():
            rows = []
            for folder in sorted(set(dirs)):
                status = status_of.get(folder)
                if status is not None and status not in finished:
                    continue               # a running sprint's folder is in use, not left
                rows.append({"sprint_id": folder if status is not None else "",
                             "status": status.value if status is not None else "unknown",
                             "path": f"{host.run_root.rstrip('/')}/{folder}"})
            if rows:
                out[name] = rows
        return out

    def ledger_status(self) -> dict:
        from coscience.pause import is_paused
        ledger = self._ledger()
        health = host_health.load(self.repo_root)
        now = time.time()
        leftover = self._leftover_by_host(ledger.pool, health)
        # One pass over every sprint/progress for every marked-or-drained host,
        # rather than one pass per host — the dashboard polls this (fix round 1, M7).
        waiting_names = {h.name for h in ledger.pool.hosts if h.removing or h.drain}
        waiting_on = host_removal.blockers_by_host(self.substrate, ledger, waiting_names)

        def cards(host) -> list[dict]:
            use = ledger.device_use(host.name)
            return [{"index": g.index, "model": g.model, "vram_gb": g.vram_gb,
                     "total_vram_gb": g.total_vram_gb,
                     "whole": use.get(g.index, (False, 0.0))[0],
                     "shared_gb": use.get(g.index, (False, 0.0))[1]}
                    for g in host.gpus]

        return {
            "capacity": dict(ledger.pool.capacity),
            "used": ledger.used(),
            "available": ledger.available(),
            "paused": is_paused(self.substrate.repo_root),
            # "" while the substrate is committing normally (B4). A repo that has
            # stopped accepting commits keeps working and loses its history silently,
            # so the one place it can be seen is here.
            "commit_error": commit_health.describe(
                commit_health.read(self.substrate.repo_root)),
            # What the capacity editor edits: the platform keys and this machine's own
            # amounts. Once remote hosts take work the totals above include them, and
            # writing a total back as this machine's capacity would be wrong.
            "local_capacity": {**{k: v for k, v in ledger.pool.capacity.items() if k in PLATFORM_KEYS},
                               **(ledger.pool.host(LOCAL).capacity if ledger.pool.host(LOCAL) else {})},
            "host_errors": list(ledger.pool.host_errors),
            "hosts": [
                {"name": h.name, "label": h.label, "ssh": h.ssh, "placeable": h.placeable,
                 "programs": list(h.programs) if h.programs is not None else None,
                 "run_root": h.run_root,
                 "capacity": dict(h.capacity),
                 # A host that cannot take work has nothing available to grant.
                 "available": ledger.available(h.name) if h.placeable else {},
                 "gpus": cards(h),
                 # The machine's totals and its switched-off cards (G2): what the server
                 # dialog shows beside what Co-Science may use.
                 "machine": dict(h.machine),
                 "cards_off": [{"index": g.index, "model": g.model, "vram_gb": g.vram_gb,
                                "total_vram_gb": g.total_vram_gb} for g in h.cards_off],
                 "shared": h.shared, "owner": h.owner, "notes": h.notes,
                 "drain": h.drain, "drained_at": h.drained_at, "removing": h.removing,
                 "waiting_on": waiting_on.get(h.name, []),
                 "health": ({"state": "local", "checked_at": 0.0, "last_ok": 0.0,
                             "fail_since": 0.0, "reason": ""}
                            if h.is_local else
                            {"state": host_health.state(health.get(h.name), now),
                             **{k: health.get(h.name, {}).get(k, default) for k, default in
                                (("checked_at", 0.0), ("last_ok", 0.0),
                                 ("fail_since", 0.0), ("reason", ""))}}),
                 "used": {k: v for k, v in ledger.used(h.name).items()
                          if k not in PLATFORM_KEYS and v},
                 # Free space per machine (B1). This one is measured live — it is not
                 # health-checked, so nothing else would ever read it; a remote host
                 # reports it on the same round trip as its liveness check.
                 **self._disk(h, health),
                 "leases": sum(1 for l in ledger.all_leases() if l.host == h.name),
                 "leftover": leftover.get(h.name, [])}
                for h in ledger.pool.hosts
            ],
            "leases": [
                {"id": l.id, "sprint_id": l.sprint_id, "amounts": l.amounts,
                 "title": self._lease_title(l.sprint_id),
                 "granted_at": l.granted_at, "expires_at": l.expires_at,
                 "priority": l.priority, "preemptible": l.preemptible,
                 "host": l.host, "gpu_devices": list(l.gpu_devices)}
                for l in ledger.all_leases()
            ],
            "stranded": [{"sprint_id": l.sprint_id, "host": l.host,
                         "listed": ledger.pool.host(l.host) is not None}
                        for l in ledger.stranded()],
        }

    def _lease_title(self, sprint_id: str) -> str:
        """What a lease is running, by name (P8, P9). One read per lease, and there are
        only ever as many leases as slots. A lease can outlive its sprint's files, so
        a missing one reads as no title rather than failing the whole status."""
        try:
            return self.substrate.load_sprint(sprint_id).title
        except FileNotFoundError:
            return ""

    def set_pause(self, paused: bool) -> dict:
        """Pause or resume the whole platform. Commits so the substrate history records
        who stopped the machine and when, the way a capacity edit does. Returns fresh
        ledger status so one round-trip re-renders the Compute page."""
        from coscience.pause import set_paused
        set_paused(self.substrate.repo_root, bool(paused))
        self.substrate.commit("paused" if paused else "resumed")
        return self.ledger_status()

    def set_capacity(self, capacity: dict, gpus: list | None = None,
                     label: str | None = None, machine: dict | None = None) -> dict:
        """Replace the declared resource pool. Validates, writes
        .coscience/resources.yaml atomically, commits, and returns fresh ledger
        status. Lowering a limit below what is currently leased is allowed and
        drains: running work keeps its lease, new grants stop.

        `gpus`, when a list, replaces this machine's GPU cards (`[]` removes them,
        keeping whatever `gpu` count `capacity` itself carries); `None` keeps
        whatever cards are already on file.

        This is this machine's own capacity. The platform limits (`workers`,
        `housekeepers`) bound agent processes wherever their work lands; the dashboard
        sets them through `set_platform_limits`. One left out of `capacity` is carried
        over from the file (G2) — the caller used to have to send them back, and an
        edit that forgot them dropped the caps. One sent is still written, for callers
        that predate the split."""
        clean_gpus: list[dict] | None = None
        if gpus is not None:
            errors = ResourcePool.from_dict({"gpus": gpus}).host_errors
            if errors:
                raise ValueError(errors[0])
            clean_gpus = [_clean_card(g) for g in gpus]

        clean: dict[str, float] = {}
        for raw_key, raw_val in (capacity or {}).items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("a resource needs a name")
            if key == "resources":
                # ResourcePool.from_dict treats a top-level `resources:` mapping as
                # the wrapper, so a resource actually named that would vanish.
                raise ValueError("'resources' is reserved and can't be a resource name")
            if key == "hosts":
                raise ValueError("'hosts' is reserved for remote machines and can't be a resource name")
            if key in ("gpus", GPU_VRAM_KEY):
                raise ValueError(f"'{key}' is reserved for GPU cards and can't be a resource name")
            if key in ("programs", "exclude_programs"):
                raise ValueError(f"'{key}' is reserved for program access and can't be a resource name")
            if key in clean:
                raise ValueError(f"duplicate resource name: {key}")
            if isinstance(raw_val, bool) or not isinstance(raw_val, (int, float)):
                raise ValueError(f"{key}: capacity must be a number")
            val = float(raw_val)
            if not math.isfinite(val):
                raise ValueError(f"{key}: capacity must be a finite number")
            if val < 0:
                raise ValueError(f"{key}: capacity can't be negative")
            clean[key] = val

        path = self.repo_root / ".coscience" / "resources.yaml"
        # The editor edits this machine's amounts and the platform keys; remote
        # hosts are declared by hand and must survive the edit untouched.
        out: dict = dict(clean)
        with pool_file_lock(self.repo_root):
            if path.is_file():
                loaded = yaml.safe_load(path.read_text()) or {}
                if isinstance(loaded, dict):
                    wrapped = loaded.get("resources")
                    hosts = (wrapped.get("hosts") if isinstance(wrapped, dict) else None) \
                        or loaded.get("hosts")
                    if hosts:
                        out["hosts"] = hosts
                    programs = (wrapped.get("programs") if isinstance(wrapped, dict) else None)
                    if programs is None:
                        programs = loaded.get("programs")
                    if programs is not None:
                        out["programs"] = programs
                    local = wrapped if isinstance(wrapped, dict) else loaded
                    for key in PLATFORM_KEYS:
                        if key in local and key not in out:
                            out[key] = local[key]
                    # The machine's totals are not capacity either; kept unless replaced below.
                    if local.get("machine"):
                        out["machine"] = local["machine"]
                    # This machine's display name is not a capacity amount, so the
                    # rebuilt document would drop it unless it is carried over.
                    on_file = (wrapped.get("label") if isinstance(wrapped, dict) else None)
                    if on_file is None:
                        on_file = loaded.get("label")
                    if on_file:
                        out["label"] = str(on_file)
                    if gpus is None:
                        file_gpus = (wrapped.get("gpus") if isinstance(wrapped, dict) else None) \
                            or loaded.get("gpus")
                        if file_gpus:
                            # The card list is the count; an edited `gpu` number would contradict it.
                            out["gpus"] = file_gpus
                            # But only drop `gpu` beside a value the parser actually accepts —
                            # dropping it beside a malformed one would leave no GPU at all.
                            if not ResourcePool.from_dict({"gpus": file_gpus}).host_errors:
                                out.pop(GPU_KEY, None)
            if label is not None:
                if _clean_label(label):
                    out["label"] = _clean_label(label)
                else:
                    out.pop("label", None)
            # This machine's totals (G2): replaced when given ({} clears them), else kept
            # from the file above.
            if machine is not None:
                if machine:
                    out["machine"] = {str(k): float(v) for k, v in machine.items()}
                else:
                    out.pop("machine", None)
            # The parser is the judge of what loads: a total below what is offered, or a
            # card offering more VRAM than it has, is refused before anything is written.
            parsed = ResourcePool.from_dict(out)
            bad = [e for e in parsed.host_errors if e.startswith(("machine", "cpu:", "memory_gb:"))
                   or "available is more than" in e]
            if bad:
                raise ValueError(bad[0])
            if gpus is not None:
                if clean_gpus:
                    out["gpus"] = clean_gpus
                    out.pop(GPU_KEY, None)
                else:
                    out.pop("gpus", None)
            self._write_resources(out)
        self.substrate.commit("capacity updated")
        return self.ledger_status()

    def set_platform_limits(self, limits: dict) -> dict:
        """Set how many agent processes the platform may run at once (G1): `workers`
        (sprint agents) and `housekeepers` (planner and wiki agents). Nothing else can
        be set here — a machine's CPUs, memory and cards are edited on its own card.

        A value of None removes the limit, which leaves that kind of agent uncapped.
        Only the named keys change; everything else in the pool file is untouched."""
        clean: dict[str, float | None] = {}
        for raw_key, raw_val in (limits or {}).items():
            key = str(raw_key).strip()
            if key not in PLATFORM_KEYS:
                raise ValueError(f"{key!r} is not a platform limit (those are "
                                 f"{', '.join(sorted(PLATFORM_KEYS))})")
            if raw_val is None:
                clean[key] = None
                continue
            if isinstance(raw_val, bool) or not isinstance(raw_val, (int, float)):
                raise ValueError(f"{key}: the limit must be a number")
            val = float(raw_val)
            if not math.isfinite(val) or val < 0:
                raise ValueError(f"{key}: the limit must be zero or more")
            clean[key] = val
        with pool_file_lock(self.repo_root):
            loaded, _hosts = self._resources_hosts()
            holder = self._local_holder(loaded)
            before = {k: holder.get(k) for k in clean}
            for key, val in clean.items():
                if val is None:
                    holder.pop(key, None)
                else:
                    holder[key] = val
            changed = any(holder.get(k) != before[k] for k in clean)
            if changed:
                self._write_resources(loaded)
        if changed:
            self.substrate.commit("platform limits updated")
        return self.ledger_status()

    def _write_resources(self, data: dict) -> None:
        write_pool_file(self.repo_root, data)

    def _resources_hosts(self) -> tuple[dict, dict]:
        """Load resources.yaml and return (the loaded document, its `hosts:` mapping).
        See `resources.pool_file_hosts` for the shape."""
        return pool_file_hosts(self.repo_root)

    def _apply_access(self, holder: dict, programs: list[str]) -> None:
        """Set one server's `programs:` key on its YAML mapping (a `hosts:` entry, or
        the top level for this machine). Always writes the key, even an empty list —
        unlike a hand-edited file, where an absent key is the only way to admit
        every program."""
        holder["programs"] = [str(p) for p in programs]
        holder.pop("exclude_programs", None)

    def _local_holder(self, loaded: dict) -> dict:
        """The mapping this machine's amounts live in: the `resources:` wrapper when
        the file uses one, else the top level."""
        wrapped = loaded.get("resources")
        return wrapped if isinstance(wrapped, dict) else loaded

    def _cut_off_pins(self) -> list[dict]:
        """Unfinished sprints pinned to a server that no longer takes their program.
        They keep their work where it is; O7's unrunnable message says what to do."""
        pool = self.pool                  # read live from resources.yaml, so it sees the write
        out = []
        for sprint in self.substrate.iter_sprints():
            if sprint.status in (SprintStatus.DONE, SprintStatus.CANCELED, SprintStatus.FAILED):
                continue
            pinned = self.substrate.load_progress(sprint.id).host
            host = pool.host(pinned) if pinned else None
            if host is not None and not host.allows(sprint.program):
                out.append({"sprint_id": sprint.id, "host": pinned})
        return out

    def set_host_programs(self, name: str, programs: list[str]) -> dict:
        """Set one server's program list from the server side, always writing the
        `programs:` key — an empty list included, so the server then admits none.
        `name` may be 'local' (this machine, edited at the top level of
        resources.yaml) or a remote server name already in `hosts:`."""
        changed = False
        with pool_file_lock(self.repo_root):
            loaded, hosts = self._resources_hosts()
            if name == LOCAL:
                holder = self._local_holder(loaded)
            elif name not in hosts:
                raise NotFoundError(f"no server {name!r} in the pool")
            else:
                holder = dict(hosts[name]) if isinstance(hosts[name], dict) else {}
            before = holder.get("programs")
            self._apply_access(holder, programs)
            if name == LOCAL:
                # Only this machine's own key is checked: another server's broken entry
                # is already reported on Compute and must not block this edit.
                _parse_programs("", holder)
            else:
                _parse_host(name, holder)                       # raises ValueError for a bad entry
                hosts[name] = holder
            changed = holder.get("programs") != before
            if changed:
                self._write_resources(loaded)
        if changed:
            self.substrate.commit(f"server {name} program access updated")
        return {**self.ledger_status(), "cut_off": self._cut_off_pins()}

    def set_program_hosts(self, program_id: str, hosts_wanted: list[str]) -> dict:
        """Set which servers take a program, from the program side. Every server in
        the pool ends up allowing `program_id` iff its name is in `hosts_wanted`."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        with pool_file_lock(self.repo_root):
            changed = self._set_program_hosts_locked(program_id, hosts_wanted)
        if changed:
            self.substrate.commit(f"program {program_id} server access updated")
        return {**self.ledger_status(), "cut_off": self._cut_off_pins()}

    def _set_program_hosts_locked(self, program_id: str, hosts_wanted: list[str]) -> bool:
        """The body of `set_program_hosts`, run with `pool_file_lock` already held.
        Returns whether anything was written (the caller decides whether to commit).

        Split out so `create_program` can hold one lock across both the program-file
        write and this access write (fix round 1, Finding 3): `pool_file_lock` is an
        `fcntl.flock`, so a second acquisition in the same process would deadlock —
        the lock must be taken exactly once, by whichever caller owns the whole
        transaction.

        I2 (fix round 1): the read, the unknown-server check and the edit
        computation all happen under the lock, built from the very document the
        lock guards (`loaded`/`hosts`) — never from a `self.pool` read before it.
        A server deleted (e.g. by the dispatcher) between an earlier unlocked read
        and this write used to read as unrecognised-but-editable, recreating a
        bare entry the parser then refused with a baffling "needs ssh". Now a
        server that isn't in `hosts_wanted`'s pool is a clear "no server named X"."""
        wanted = set(hosts_wanted)
        loaded, hosts = self._resources_hosts()
        pool = ResourcePool.from_dict(loaded)
        pool_names = {h.name for h in pool.hosts}
        unknown = [n for n in hosts_wanted if n not in pool_names]
        if unknown:
            raise ValueError(f"no server named {unknown[0]!r}")

        # Every current program id, to reify an absent (every-program) list into
        # an explicit one before editing it. Read under the same lock hold as the
        # edit below, so a caller that also writes the program file first (e.g.
        # create_program) sees its own new program here — never a stale snapshot
        # taken before that program existed.
        all_program_ids = [p.id for p in self.substrate.iter_programs()]

        # Compute every server's new programs list before writing anything, so
        # a refusal partway through never leaves a partial edit.
        edits: list[tuple[str, list[str]]] = []
        for host in pool.hosts:
            want = host.name in wanted
            has = host.allows(program_id)
            if want == has:
                continue
            programs = list(host.programs) if host.programs is not None else list(all_program_ids)
            if want:
                if program_id not in programs:
                    programs = programs + [program_id]
            else:
                programs = [p for p in programs if p != program_id]
            edits.append((host.name, programs))

        if not edits:
            return False
        for name, programs in edits:
            if name == LOCAL:
                self._apply_access(self._local_holder(loaded), programs)
            else:
                entry = dict(hosts.get(name) or {})
                self._apply_access(entry, programs)
                _parse_host(name, entry)                    # raises ValueError for a bad entry
                hosts[name] = entry
        self._write_resources(loaded)
        return True

    def remove_host(self, name: str) -> dict:
        """Mark a server for removal: it takes no new grants, and the dispatcher
        deletes it at the start of a cycle, before granting, once nothing is on it
        (`host_removal.blockers`) — no human timing involved. Its probe record and
        any run directories on the host itself are left alone. Calling this again
        on an already-marked server is harmless."""
        if name == LOCAL:
            raise ValueError("this machine cannot be removed; use Pause to stop new work here")
        with pool_file_lock(self.repo_root):
            loaded, hosts = self._resources_hosts()
            if name not in hosts:
                raise NotFoundError(f"no host {name!r} in the pool")
            if not isinstance(hosts[name], dict):
                raise ValueError(f"hosts.{name}: is not a mapping; fix the file first")
            entry = dict(hosts[name])
            entry["remove"] = True
            _parse_host(name, entry)
            hosts[name] = entry
            self._write_resources(loaded)
        self.substrate.commit(f"server {name} marked for removal")
        return self.ledger_status()

    def keep_host(self, name: str) -> dict:
        """Take back a remove or drain mark; the server takes new grants again."""
        if name == LOCAL:
            raise ValueError("this machine is always in the pool; there is nothing to keep")
        with pool_file_lock(self.repo_root):
            loaded, hosts = self._resources_hosts()
            if name not in hosts:
                raise NotFoundError(f"no host {name!r} in the pool")
            if not isinstance(hosts[name], dict):
                raise ValueError(f"hosts.{name}: is not a mapping; fix the file first")
            entry = dict(hosts[name])
            entry.pop("remove", None)
            entry.pop("drain", None)
            entry.pop("drained_at", None)
            _parse_host(name, entry)
            hosts[name] = entry
            self._write_resources(loaded)
        self.substrate.commit(f"server {name} kept in the pool")
        return self.ledger_status()

    # --- onboarding (O5) ---
    # Dot-only names ("." or "..") are refused: `_host_probe_path`/`survey_thread_dir`
    # join the name as a path segment, so an unvalidated ".." would escape into
    # `.coscience` itself (review round 1, I2).
    _HOST_NAME = re.compile(r"^(?!\.+$)[A-Za-z0-9._-]+$")

    def _host_probe_path(self, name: str) -> Path:
        return self.repo_root / ".coscience" / "host-probes" / f"{name}.json"

    @staticmethod
    def _append_notes(existing: str, lines: str) -> str:
        """Append `lines` (one per newline) to `existing` notes, skipping any line
        already present — a second target change that fails the same check must not
        duplicate its Override line (review round 1, M4)."""
        if not lines:
            return existing
        have = set(existing.splitlines())
        to_add = [ln for ln in lines.splitlines() if ln not in have]
        if not to_add:
            return existing
        return existing + ("\n" if existing else "") + "\n".join(to_add)

    def _override_notes_or_raise(self, name: str, record: dict) -> str:
        """The `Override <check>: <reason>` lines to append to a server's notes when
        a human accepts a failed check on the agent's written say-so (O11). Raises
        ValueError when the survey is still running, its proposal is missing,
        invalid or silent on some failed check, or was written against a probe
        other than the one currently on record (review round 1, I1) — an override
        must be judged against exactly what the agent was shown."""
        from coscience import host_survey
        thread = self.substrate.load_survey_thread(name)
        if thread is not None and thread.pending:
            raise ValueError("the survey is still working; wait for it to finish")
        tdir = self.substrate.survey_thread_dir(name)
        ref = host_survey.read_probe_ref(tdir)
        if ref is None:
            raise ValueError(f"no survey has run against the current probe of {name}; survey it first")
        if not host_survey.probe_ref_matches(ref, record):
            raise ValueError(f"the survey ran against an older probe of {name}; ask the agent to look again")
        proposal, proposal_error = host_survey.read_proposal(tdir, record)
        if proposal_error:
            raise ValueError(proposal_error)
        return host_survey.override_notes(record, proposal)

    def probe_host(self, *, name: str, ssh: str, run_root: str = "", shared: bool = False,
                   programs: list | None = None,
                   owner: str = "", notes: str = "", runner=None) -> dict:
        """Probe a server and record what was found, for a human to confirm. Nothing
        enters the pool here."""
        from coscience import host_probe
        name = str(name or "").strip()
        if not self._HOST_NAME.match(name) or name == "local":
            raise ValueError("a host name uses letters, digits, '.', '_' or '-' and is not 'local'")
        ssh = str(ssh or "").strip()
        host_probe.ssh_argv(ssh)                           # refuses a bad target before anything runs
        run_root = host_probe.check_run_root(
            str(run_root or "").strip() or host_probe.DEFAULT_RUN_ROOT)  # refuses an unsafe run root
        declared = {"ssh": ssh, "run_root": run_root, "shared": bool(shared),
                    # None (omitted) stays None here rather than collapsing to []:
                    # the dashboard's pre-seed guard sends no `programs` at all when
                    # it hasn't loaded the ledger yet, and that must read back as
                    # "not specified," distinct from an explicit empty list (fix
                    # round 2, New Issue 3).
                    "programs": [str(p) for p in programs] if programs is not None else None,
                    "owner": str(owner or ""), "notes": str(notes or "")}
        result = host_probe.probe_host(ssh, declared["run_root"], shared=declared["shared"],
                                       runner=runner or host_probe.subprocess_runner)
        record = {"name": name, "declared": declared, "probed_at": time.time(), **result}
        path = self._host_probe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2))
        self.substrate.commit(f"host {name} probed")
        return record

    def list_host_probes(self) -> list[dict]:
        folder = self.repo_root / ".coscience" / "host-probes"
        records = []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return records

    # --- agent survey of a probed server (O11) ---
    def survey_host(self, name: str, message: str = "", launch=None) -> dict:
        """Start or continue an agent's full-scope survey of a probed server: a
        resumable Claude session scoped to the server (`.coscience/host-surveys/<name>/`),
        built on program chat's thread store, launch, collection, gates and call log.
        `launch(**kwargs)->token` is injectable for tests."""
        from coscience import chat_agent, host_survey, usage_meter
        from coscience.worker import claude_usage_ok
        if not self._HOST_NAME.match(name or ""):
            raise ValueError("a host name uses letters, digits, '.', '_' or '-' and is not all dots")
        path = self._host_probe_path(name)
        if not path.is_file():
            raise NotFoundError(f"no probe recorded for host {name!r}")
        record = json.loads(path.read_text())
        if not record.get("ok"):
            raise ValueError(f"the last probe of {name} could not log in; fix SSH and probe again")
        thread = self.substrate.load_survey_thread(name)
        message = str(message or "").strip()
        if thread is not None:
            if not message:
                raise ValueError("message is required")
            if thread.pending:
                raise ValueError("the survey is still working on the previous message")
        else:
            thread = ChatThread(id=name, title=f"Survey of {name}", scope="full",
                                session_id=str(uuid4()), created_at=time.time())
        # Pause first: it also fails the usage gate below, but a reset does not clear
        # it — Resume does — so the human must be told which stop this is. Mirrors
        # post_chat_message's gates; a survey has no thread to append a system
        # message into on refusal, so it raises instead.
        if launch is None and is_paused(self.substrate.repo_root):
            raise ValueError("Paused — Resume in Compute to keep talking.")
        if launch is None and not claude_usage_ok(repo_root=self.substrate.repo_root):
            raise ValueError("Claude usage is exhausted — please try again after the reset.")
        resume = thread.turns_done > 0
        tdir = self.substrate.survey_thread_dir(name)
        if resume:
            prompt = message
            # A re-probe since the last turn invalidates whatever the agent last
            # saw; tell it plainly rather than let it write overrides against
            # stale facts (review round 1, I1).
            if not host_survey.probe_ref_matches(host_survey.read_probe_ref(tdir), record):
                prompt = host_survey.render_reprobe_notice(record) + "\n\nHuman: " + message
        else:
            entry = self._resources_hosts()[1].get(name)
            prompt = host_survey.render_first_prompt(record, entry)
            if message:
                prompt += "\n\nHuman: " + message
        if message:
            thread.messages.append({"role": "user", "text": message, "at": time.time()})
        tdir.mkdir(parents=True, exist_ok=True)
        host_survey.ensure_gitignore(tdir)
        launch = launch or chat_agent.launch_turn
        token = launch(thread_dir=tdir, workdir=str(tdir), prompt=prompt, scope="full",
                       session_id=thread.session_id, resume=resume, model="")
        # Written only once the turn is launched, so a later accept judges the agent's
        # overrides against exactly the probe it was shown. Writing it before a launch
        # that then fails would vouch for a proposal the agent wrote about an older probe.
        host_survey.write_probe_ref(tdir, record)
        thread.pending, thread.agent_token = True, str(token)
        thread.agent_call = usage_meter.start_call(
            self.substrate.repo_root, "survey", limits=usage_meter.current_window(), token=str(token))
        thread.messages = thread.messages[-200:]
        self.substrate.save_survey_thread(name, thread)
        self.substrate.commit(f"server {name}: survey message")
        # Shaped like get_survey(name), but built directly rather than through it:
        # get_survey collects a pending turn before reporting, and the turn this
        # call just launched has had no chance yet to even write its first byte —
        # collecting it here would race a real launch and misreport it as dead on
        # every single call (mirrors why post_chat_message never re-collects either).
        return self._survey_state(name, thread)

    def get_survey(self, name: str) -> dict:
        """The state of a server's survey: pending/finished messages, and the agent's
        proposal once it has written one (validated against the current probe record).
        Collects a finished turn first, as chat reads do."""
        from coscience import host_survey
        if not self._HOST_NAME.match(name or ""):
            raise ValueError("a host name uses letters, digits, '.', '_' or '-' and is not all dots")
        thread = self.substrate.load_survey_thread(name)
        if thread is not None and thread.pending:
            thread = host_survey.collect(self.substrate, name, thread)
        return self._survey_state(name, thread)

    def _survey_state(self, name: str, thread) -> dict:
        from coscience import host_survey
        proposal, proposal_error = None, ""
        tdir = self.substrate.survey_thread_dir(name)
        path = self._host_probe_path(name)
        if path.is_file():
            record = json.loads(path.read_text())
            proposal, proposal_error = host_survey.read_proposal(tdir, record)
            if proposal is not None and not host_survey.probe_ref_matches(
                    host_survey.read_probe_ref(tdir), record):
                # A stale proposal is never offered, even though it parses cleanly:
                # it was written about a probe that is no longer current (I1).
                proposal, proposal_error = None, (
                    f"the survey ran against an older probe of {name}; ask the agent to look again")
        elif (tdir / "proposal.json").is_file():
            # The probe record is gone (removed, or re-added under the same name)
            # but a stale proposal file is still sitting there — never show it (M6).
            proposal_error = f"the probe record for {name} is gone; probe it again"
        return {
            "name": name,
            "pending": bool(thread.pending) if thread is not None else False,
            "messages": list(thread.messages) if thread is not None else [],
            "proposal": proposal,
            "proposal_error": proposal_error,
            "started": thread is not None,
        }

    def confirm_host(self, *, name: str, capacity: dict, gpus: list | None = None,
                     probed_at: float | None = None, accept_overrides: bool = False,
                     notes: str | None = None, programs: list | None = None,
                     machine: dict | None = None) -> dict:
        """Write a probed server into `resources.yaml` `hosts:`, keeping the rest of the
        file. The entry is checked by the same parser the pool uses, so what is written
        is what loads."""
        path = self._host_probe_path(str(name or ""))
        if not self._HOST_NAME.match(str(name or "")) or not path.is_file():
            raise NotFoundError(f"no probe recorded for host {name!r}")
        record = json.loads(path.read_text())
        if not record.get("ok"):
            raise ValueError(f"the last probe of {name} failed; probe it again before adding it")
        if probed_at is not None and abs(probed_at - record["probed_at"]) > 1e-6:
            raise ValueError(f"the probe of {name} changed since it was reviewed; probe it again")
        failed = [c.get("name", "?") for c in record.get("checks", []) if not c.get("ok")]
        override_lines = ""
        if failed:
            if not accept_overrides:
                raise ValueError(f"checks failed on {name}: {', '.join(failed)}; fix them on the "
                                 "server and probe again")
            override_lines = self._override_notes_or_raise(name, record)
        declared = record["declared"]
        entry: dict = {"ssh": declared["ssh"], "run_root": declared["run_root"],
                       "capacity": {str(k): float(v) for k, v in (capacity or {}).items()},
                       "gpus": ([_clean_card(g) for g in gpus] if gpus is not None
                                else record["proposal"].get("gpus", []))}
        # The machine's totals: what the caller says, else what the probe found (G2).
        totals = machine if machine is not None else record.get("proposal", {}).get("machine")
        if totals:
            entry["machine"] = {str(k): float(v) for k, v in totals.items()}
        if not entry["gpus"]:
            del entry["gpus"]
            proposed_capacity = record.get("proposal", {}).get("capacity", {})
            if "gpu" in proposed_capacity:
                entry["capacity"].setdefault("gpu", proposed_capacity["gpu"])
        for key in ("shared", "owner", "notes"):
            if declared.get(key):
                entry[key] = declared[key]
        # The program list is written whenever the caller gives one — the dashboard's Add
        # form always does — including an empty list, which means the server takes no work.
        # With no caller list, the declaration's is used, and a declaration with none
        # leaves the key out, so the server admits every program until it is configured.
        if programs is not None:
            entry["programs"] = [str(p) for p in programs]
        elif declared.get("programs"):
            entry["programs"] = list(declared["programs"])
        # A caller-given `notes` (the dashboard's Add form, which may carry the
        # agent's survey notes) replaces whatever the declaration held, before any
        # override lines are appended below (review round 1, C2) — otherwise the
        # human's edited notes silently reverted to the probe's declared ones.
        if notes is not None:
            if notes:
                entry["notes"] = notes
            else:
                entry.pop("notes", None)
        if override_lines:
            entry["notes"] = self._append_notes(entry.get("notes", ""), override_lines)
        _parse_host(name, entry)                           # raises ValueError for a bad entry

        with pool_file_lock(self.repo_root):
            loaded, hosts = self._resources_hosts()
            hosts[name] = entry
            self._write_resources(loaded)
        self.substrate.commit(f"host {name} added to the pool")
        return self.ledger_status()

    def detect_local(self, runner=None) -> dict:
        """This machine's own facts and a proposed capacity — the same probe script
        onboarding runs over SSH, run locally instead. Nothing is written."""
        from coscience import host_probe
        return host_probe.detect_local(runner=runner or host_probe.subprocess_runner)

    def update_host(self, name: str, *, ssh: str | None = None, run_root: str | None = None,
                    shared: bool | None = None, programs: list | None = None,
                    owner: str | None = None, notes: str | None = None,
                    capacity: dict | None = None, gpus: list | None = None,
                    label: str | None = None, machine: dict | None = None,
                    probed_at: float | None = None, accept_overrides: bool = False) -> dict:
        """Edit a remote host's entry in place, keeping `drain` and anything not
        given here. A new SSH target or run root is refused unless a probe of it is
        already on record and passed — the same bar `confirm_host` holds a new
        server to."""
        from coscience import host_probe
        with pool_file_lock(self.repo_root):
            loaded, hosts = self._resources_hosts()
            if name == LOCAL or name not in hosts:
                raise NotFoundError(f"no remote host {name!r} in the pool")
            entry = hosts[name]
            if not isinstance(entry, dict):
                raise ValueError(f"hosts.{name}: is not a mapping; fix the file first")

            new_ssh = ssh if ssh is not None else entry["ssh"]
            new_root = run_root if run_root is not None else entry.get("run_root", host_probe.DEFAULT_RUN_ROOT)
            ssh_changed = new_ssh != entry["ssh"]
            root_changed = new_root != entry.get("run_root", host_probe.DEFAULT_RUN_ROOT)
            override_lines = ""
            if ssh_changed or root_changed:
                message = (f"probe {name} with the new SSH target first" if ssh_changed
                          else f"probe {name} with the new run root first")
                path = self._host_probe_path(name)
                record = json.loads(path.read_text()) if path.is_file() else None
                if (record is None or record["declared"]["ssh"] != new_ssh
                        or record["declared"]["run_root"] != new_root):
                    raise ValueError(message)
                if not record.get("ok"):
                    raise ValueError(f"the last probe of {name} failed; probe it again before applying it")
                failed = [c.get("name", "?") for c in record.get("checks", []) if not c.get("ok")]
                if failed:
                    if not accept_overrides:
                        raise ValueError(f"checks failed on {name}: {', '.join(failed)}; fix them on the "
                                         "server and probe again")
                    override_lines = self._override_notes_or_raise(name, record)
                if probed_at is not None and abs(probed_at - record["probed_at"]) > 1e-6:
                    raise ValueError(f"the probe of {name} changed since it was reviewed; probe it again")

            new_entry = dict(entry)
            new_entry["ssh"] = new_ssh
            new_entry["run_root"] = new_root
            if shared is not None:
                if shared:
                    new_entry["shared"] = True
                else:
                    new_entry.pop("shared", None)
            if owner is not None:
                if owner:
                    new_entry["owner"] = owner
                else:
                    new_entry.pop("owner", None)
            if notes is not None:
                if notes:
                    new_entry["notes"] = notes
                else:
                    new_entry.pop("notes", None)
            if label is not None:
                # What a human calls it, never what anything keys on: the name stays
                # the identity every lease, progress file and probe is written under.
                new_entry["label"] = _clean_label(label)
                if not new_entry["label"]:
                    new_entry.pop("label")
            if programs is not None:
                self._apply_access(new_entry, programs)
            if capacity is not None:
                new_entry["capacity"] = {str(k): float(v) for k, v in capacity.items()}
            if gpus is not None:
                if gpus:
                    new_entry["gpus"] = [_clean_card(g) for g in gpus]
                else:
                    new_entry.pop("gpus", None)
                # The dialog echoes the host's current capacity, which carries a derived
                # `gpu: <old count>` — that stale count must not fight the new card list
                # (or its absence) in _parse_host, so the count is derived fresh from `gpus`.
                new_entry["capacity"] = {k: v for k, v in new_entry.get("capacity", {}).items()
                                         if k != GPU_KEY}

            if machine is not None:
                if machine:
                    new_entry["machine"] = {str(k): float(v) for k, v in machine.items()}
                else:
                    new_entry.pop("machine", None)

            if override_lines:
                new_entry["notes"] = self._append_notes(new_entry.get("notes", ""), override_lines)

            _parse_host(name, new_entry)                       # raises ValueError for a bad entry
            hosts[name] = new_entry
            self._write_resources(loaded)
        self.substrate.commit(f"host {name} configuration updated")
        return {**self.ledger_status(), "cut_off": self._cut_off_pins()}

    # --- wiki (phase 2: read) -------------------------------------------------

    def wiki_page_path(self, program_id: str, slug: str) -> Path:
        """Guarded path to one bundle page. `slug` is the bundle-relative path
        without `.md` (`concepts/auth-gate`).

        A slug arrives from a URL segment, so it is a traversal primitive: resolve
        first, then prove containment, exactly as _guarded_file does for artifact
        versions. Symlink resolution happens before the check, which is why this
        cannot be a string comparison."""
        from coscience import wiki_store
        try:
            root = wiki_store.bundle_dir(self.substrate, program_id).resolve()
            path = (root / f"{slug}.md").resolve()
        except (ValueError, OSError):
            raise NotFoundError(slug)
        if not path.is_file() or not path.is_relative_to(root):
            raise NotFoundError(slug)
        return path

    def _wiki_pages(self, program_id: str):
        from coscience import wiki_store
        return wiki_store.iter_pages(self.substrate, program_id)

    def wiki_summary(self, program_id: str) -> dict:
        from coscience import wiki_read, wiki_store
        pages = self._wiki_pages(program_id)
        state = wiki_store.load_state(self.substrate, program_id)
        pending = len(wiki_store.pending_objects(
            self.substrate, program_id, state.get("ingested") or {},
            set(state.get("quarantined") or [])))
        counts = self.wiki_lint_report(program_id)["counts"]
        try:
            index_md = (wiki_store.bundle_dir(self.substrate, program_id)
                        / "index.md").read_text()
        except OSError:
            index_md = ""
        # The wiki's own settings ride along with its summary: the browse view is
        # where you are looking when you form an opinion about how the pages read,
        # so it is where the model that wrote them should be changeable.
        try:
            program = self.substrate.load_program(program_id)
            model, enabled = program.wiki_model, program.wiki_enabled
        except (OSError, ValueError):
            program, model, enabled = None, "", True
        out = wiki_read.summary(pages, state, pending, counts, index_md,
                                wiki_model=model, wiki_enabled=enabled)
        # Merge policy and its pending queue ride along with the rest of the
        # wiki's own settings — same reasoning as wiki_model/wiki_enabled above.
        out["wiki_merge"] = getattr(program, "wiki_merge", "auto")
        out["merge_proposals"] = len(state.get("merge_proposals") or [])
        return out

    def list_wiki_pages(self, program_id: str) -> list[dict]:
        from coscience import wiki_read
        return [{"path": p.path, "slug": p.slug, "type": p.type,
                 "title": p.title or p.slug, "status": p.status,
                 "trust": wiki_read.trust_tier(p), "stale_after": p.stale_after,
                 "tags": list(p.tags)}
                for p in sorted(self._wiki_pages(program_id), key=lambda p: p.path)]

    def get_wiki_page(self, program_id: str, slug: str) -> dict:
        from coscience import wiki_read, wiki_store
        self.wiki_page_path(program_id, slug)          # guard before reading
        page = wiki_store.read_page(self.substrate, program_id, f"{slug}.md")
        if page is None:
            raise NotFoundError(slug)
        return wiki_read.page_detail(page, self._wiki_pages(program_id), program_id)

    def search_wiki(self, program_id: str, q: str, limit: int = 50) -> list[dict]:
        from coscience import wiki_read
        return wiki_read.search(self._wiki_pages(program_id), q, limit=limit)

    def wiki_log(self, program_id: str) -> str:
        from coscience import wiki_store
        try:
            return (wiki_store.bundle_dir(self.substrate, program_id)
                    / "log.md").read_text()
        except OSError:
            return ""

    def wiki_graph(self, program_id: str) -> dict:
        """The concept graph for this program's bundle (spec 11.1).

        Read-only and cached; the builder is pure and lives in wiki_graph."""
        from coscience import wiki_graph
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        return wiki_graph.cached_build(self.substrate, program_id)

    def wiki_citations(self, program_id: str, oid: str) -> list[dict]:
        """Which wiki pages cite this result or artifact version (design 7)."""
        from coscience import wiki_read, wiki_store
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        return wiki_read.citing_pages(
            wiki_store.iter_pages(self.substrate, program_id), oid)

    def wiki_lint_report(self, program_id: str) -> dict:
        """Live findings, never autofixed, plus the agent's own filed summaries
        (spec 11.1). `fix=True` here would mean a GET mutated the bundle."""
        from coscience import wiki_lint, wiki_store
        findings, _ = wiki_lint.run_lint(self.substrate, program_id, fix=False)
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        d = wiki_store.state_dir(self.substrate, program_id) / "lint"
        reports = []
        try:
            files = sorted(d.glob("*.md"), key=lambda f: f.stem, reverse=True)
        except OSError:
            files = []
        for f in files:
            try:
                reports.append({"date": f.stem, "text": f.read_text()})
            except OSError:
                continue
        return {"counts": counts,
                "findings": [{"rule": f.rule, "severity": f.severity,
                              "path": f.path, "message": f.message} for f in findings],
                "reports": reports}

    # --- wiki (phase 2: curation) --------------------------------------------

    _WIKI_STATUSES = ("draft", "stable", "deprecated")

    def _load_wiki_page(self, program_id: str, slug: str):
        from coscience import wiki_store
        self.wiki_page_path(program_id, slug)
        page = wiki_store.read_page(self.substrate, program_id, f"{slug}.md")
        if page is None:
            raise NotFoundError(slug)
        return page

    def _save_wiki_page(self, program_id: str, page, message: str) -> dict:
        from coscience import wiki_read, wiki_store
        wiki_store.write_page(self.substrate, program_id, page)
        self.substrate.commit(message)
        return wiki_read.page_detail(page, self._wiki_pages(program_id), program_id)

    def verify_wiki_page(self, program_id: str, slug: str, by: str,
                         now: float | None = None) -> dict:
        """Append one OKF `verified` entry. Appends rather than replaces: the trust
        tier is derived from the whole list, and who checked a page previously is
        part of its record."""
        page = self._load_wiki_page(program_id, slug)
        page.verified = list(page.verified or []) + [
            {"by": by, "at": float(now if now is not None else time.time())}]
        return self._save_wiki_page(program_id, page,
                                    f"wiki {program_id}: verified {slug} by {by}")

    def set_wiki_page_status(self, program_id: str, slug: str, status: str) -> dict:
        if status not in self._WIKI_STATUSES:
            raise ValueError(f"status must be one of {self._WIKI_STATUSES}: {status}")
        page = self._load_wiki_page(program_id, slug)
        page.status = status
        return self._save_wiki_page(program_id, page,
                                    f"wiki {program_id}: {slug} status {status}")

    def run_wiki(self, program_id: str, kind: str = "ingest", agent=None,
                 by: str | None = None) -> dict:
        """Force one wiki beat now.

        The usage gate is forced open: a human pressing the button is a stronger
        signal than the gate, which exists to stop *unattended* loops burning a
        window. `kind="lint"` pushes ingests_since_lint to the threshold and lets
        beat() decide, rather than adding a second definition of what a run is.

        `by` is the actor the caller resolved from the session, recorded on the
        run because a forced run spends a Claude window on someone's say-so."""
        from coscience import wiki, wiki_store
        if kind not in ("ingest", "lint"):
            raise ValueError(f"kind must be ingest or lint: {kind}")
        program = self.substrate.load_program(program_id)
        if kind == "lint":
            with wiki_store.state_guard(self.substrate, program_id) as state:
                state["ingests_since_lint"] = max(int(state.get("ingests_since_lint", 0)),
                                                  wiki.lint_every())
        real = agent if agent is not None else self._wiki_agent()
        line = wiki.beat(self.substrate, program, time.time(), real,
                         usage_gate=lambda: True, forced_by=by)
        return {"line": line or "wiki: nothing to do"}

    def _wiki_agent(self):
        from coscience import wiki_agent
        return wiki_agent.WikiAgent()

    def unquarantine_wiki(self, program_id: str) -> dict:
        """Clear the quarantine list so the objects become pending again. Their
        ingested entries are untouched — pending_objects re-derives from hashes."""
        from coscience import wiki_store
        with wiki_store.state_guard(self.substrate, program_id) as state:
            cleared = list(state.get("quarantined") or [])
            state["quarantined"] = []
        if cleared:
            self.substrate.commit(f"wiki {program_id}: unquarantined {len(cleared)}")
        return {"cleared": cleared}

    def delete_wiki_page(self, program_id: str, slug: str) -> dict:
        """Delete a page and drop every typed relation pointing at it.

        Body links are deliberately left dangling: a broken link a reader can see
        is honest, while a dangling typed relation silently breaks the containment
        invariant rel/no-link exists to enforce."""
        from coscience import wiki_store
        path = self.wiki_page_path(program_id, slug)
        rel = f"{slug}.md"
        dropped = []
        for other in self._wiki_pages(program_id):
            if other.path == rel:
                continue
            keep = [r for r in other.relations
                    if (r.target or "").split("#", 1)[0].strip().lstrip("/") != rel]
            if len(keep) != len(other.relations):
                dropped += [{"path": other.path, "type": r.type}
                            for r in other.relations if r not in keep]
                other.relations = keep
                wiki_store.write_page(self.substrate, program_id, other)
        path.unlink()
        self.substrate.commit(f"wiki {program_id}: deleted {rel}")
        return {"deleted": rel, "relations_dropped": dropped}

    def merge_wiki_pages(self, program_id: str, winner: str, loser: str) -> dict:
        """Fold `loser` into `winner`, rewrite everything that pointed at it, and
        delete it — in one commit.

        Deliberately NOT built on delete_wiki_page: that drops inbound relations,
        which is right for a deletion and wrong here. A merge retargets them, or
        it discards exactly the knowledge it exists to preserve (spec 9.1).

        Nothing gates this in `auto` but git, so the commit is the undo and names
        both pages."""
        from coscience import wiki_merge, wiki_store
        win = self._load_wiki_page(program_id, _strip_md(winner))
        lose = self._load_wiki_page(program_id, _strip_md(loser))
        others = [p for p in self._wiki_pages(program_id)
                  if p.path not in (win.path, lose.path)]
        result = wiki_merge.plan(win, lose, others)      # ValueError on a refusal

        wiki_store.write_page(self.substrate, program_id, result.winner)
        for page in result.rewritten:
            wiki_store.write_page(self.substrate, program_id, page)
        self.wiki_page_path(program_id, _strip_md(loser)).unlink()
        sha = self.substrate.commit(
            f"wiki {program_id}: merged {result.loser_path} into {result.winner.path}")
        return {"winner": result.winner.path, "loser": result.loser_path,
                "rewritten": [p.path for p in result.rewritten], "commit": sha}

    # --- wiki (phase 3: merge proposals, activity, policy) --------------------

    _WIKI_MERGE_POLICIES = ("auto", "propose")

    def list_wiki_merges(self, program_id: str) -> list[dict]:
        from coscience import wiki_store
        state = wiki_store.load_state(self.substrate, program_id)
        return list(state.get("merge_proposals") or [])

    def _take_proposal(self, state: dict, merge_id: str) -> dict:
        pending = list(state.get("merge_proposals") or [])
        for i, entry in enumerate(pending):
            if str(entry.get("id")) == merge_id:
                state["merge_proposals"] = pending[:i] + pending[i + 1:]
                return entry
        raise NotFoundError(merge_id)

    def accept_wiki_merge(self, program_id: str, merge_id: str) -> dict:
        """Apply a queued merge now.

        Now, rather than at the next beat: a human who clicked accept and saw
        nothing happen has no way to tell approval from a bug (spec 9.1). Prose
        is tidied later by the lint run the merged_from marker summons."""
        from coscience import wiki_store
        with wiki_store.state_guard(self.substrate, program_id) as state:
            entry = self._take_proposal(state, merge_id)
        winner, loser = entry["winner"], entry["loser"]
        try:
            out = self.merge_wiki_pages(program_id, winner, loser)
        except (NotFoundError, ValueError):
            # Re-checked at apply time on purpose: pages move between an agent
            # proposing and a human clicking.
            with wiki_store.state_guard(self.substrate, program_id) as state:
                state.setdefault("merges_refused", []).append(sorted([winner, loser]))
            return {"applied": False, "winner": winner, "loser": loser, "rewritten": []}
        except Exception:
            # Not a judgement that the merge was wrong — e.g. a genuine race
            # against another process raising an OSError out of commit()/unlink().
            # Spec 9.1: nothing gates an automatic merge but git, so the whole
            # safety story is "you can see what happened" — a proposal must
            # never evaporate on an unexpected error. Put it back and let the
            # caller see the error.
            with wiki_store.state_guard(self.substrate, program_id) as state:
                pending = list(state.get("merge_proposals") or [])
                pending.append(entry)
                state["merge_proposals"] = pending
            raise
        # Amends spec §11.3 (ruled 2026-08-28): Activity shows everything that
        # changed the wiki, not only what runs did. `by: "human"` keeps this
        # entry from being mistaken for an agent's work in the same list.
        from coscience.wiki import RUNS_KEPT
        with wiki_store.state_guard(self.substrate, program_id) as state:
            state["runs"] = ([{
                "id": merge_id, "kind": "merge", "by": "human",
                "status": "ok", "at": time.time(),
                "pages_created": 0, "pages_updated": len(out.get("rewritten") or []),
                "merged": [{"loser": loser, "winner": winner,
                            "commit": out.get("commit", "")}],
            }] + list(state.get("runs") or []))[:RUNS_KEPT]
        return {"applied": True, **out}

    def reject_wiki_merge(self, program_id: str, merge_id: str) -> dict:
        from coscience import wiki_store
        with wiki_store.state_guard(self.substrate, program_id) as state:
            entry = self._take_proposal(state, merge_id)
            pair = sorted([entry["winner"], entry["loser"]])
            state.setdefault("merges_refused", []).append(pair)
        return {"rejected": pair}

    def wiki_activity(self, program_id: str) -> list[dict]:
        from coscience import wiki_store
        return list(wiki_store.load_state(self.substrate, program_id).get("runs") or [])

    def set_program_wiki_merge(self, program_id: str, policy: str) -> dict:
        """auto merges duplicates unattended; propose queues them for a human.

        Nothing gates auto but git — each merge is its own commit and that commit
        is the undo. Ruled deliberately (spec 9.1)."""
        if policy not in self._WIKI_MERGE_POLICIES:
            raise ValueError(f"policy must be auto or propose: {policy}")
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.wiki_merge = policy
        self.substrate.save_program(program)
        return {"id": program_id, "wiki_merge": policy}

    def set_wiki_human_notes(self, program_id: str, slug: str, text: str) -> dict:
        """Replace the protected `# Human notes` section, and nothing else.

        This is the only writer of that section (spec 6.2) — agents must never
        touch it, which is why it has an endpoint of its own instead of free-form
        page editing."""
        page = self._load_wiki_page(program_id, slug)
        page.body = _replace_section(page.body, "Human notes", text)
        return self._save_wiki_page(program_id, page,
                                    f"wiki {program_id}: human notes on {slug}")


def _strip_md(path: str) -> str:
    """Page paths cross the API as `concepts/a.md`; the guarded loaders take a
    slug without the extension. One place to convert, so the guard is never
    accidentally bypassed by a caller that forgot."""
    return path[:-3] if path.endswith(".md") else path


def _replace_section(body: str, heading: str, text: str) -> str:
    """Swap the body text under `# <heading>`, appending the section if absent.

    Mirrors wiki_okf.Page.section's view of a section — from its heading to the
    next `# ` — so a read after a write returns exactly what was written."""
    import re
    marks = [(m.group(1), m.start(), m.end())
             for m in re.finditer(r"(?m)^# +(.+?)\s*$", body)]
    block = f"# {heading}\n\n{text.strip()}\n"
    for i, (name, start, _end) in enumerate(marks):
        if name.strip().lower() == heading.strip().lower():
            stop = marks[i + 1][1] if i + 1 < len(marks) else len(body)
            return body[:start] + block + ("\n" + body[stop:] if stop < len(body) else "")
    return body.rstrip("\n") + "\n\n" + block
