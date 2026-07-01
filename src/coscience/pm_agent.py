"""The PM heartbeat: gather context, call the reasoner once (fenced behind an
atomic staging commit), then idempotently submit proposed sprints + write the
report. Deterministic and kill-safe; the reasoner does no writes."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass

from coscience import usage_meter
from coscience.models import Sprint, SprintStatus, Idea
from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint, coerce_resources

# The PM may not push the program past this many sprints awaiting human review.
# Humans can propose beyond it; this only gates the PM's own proposing/promoting.
MAX_PROPOSED = 4


def _context_payload(context: PMContext) -> dict:
    """The per-category inputs the PM reacts to. The PM's own pending proposals
    (status 'proposed') are deliberately excluded — they are its output, not new
    input, so proposing does not re-trigger the next cycle."""
    return {
        "goals": context.goals,
        "guidance": sorted(context.human_guidance),
        "active": sorted((s["id"], s["status"]) for s in context.open_sprints
                         if s["status"] != SprintStatus.PROPOSED.value),
        "completed": sorted((s["id"], s["result"]) for s in context.completed),
        "failed": sorted((s["id"], s["error"]) for s in context.failed),
        "sprint_feedback": sorted((f["sprint_id"], c)
                                  for f in context.sprint_feedback for c in f["comments"]),
        # Human idea signal re-triggers the PM; its own pm-sourced ideas/summary do not.
        "human_ideas": sorted(i["text"] for i in context.ideas if i.get("source") == "human"),
        # gather_context flattens idea comments to plain strings (not dicts), same as
        # sprint_feedback above — index them as strings.
        "idea_comments": sorted(str(c) for i in context.ideas for c in i.get("comments", [])),
    }


def context_fingerprint(context: PMContext) -> str:
    """A stable hash over all the inputs — unchanged hash means nothing to react to."""
    blob = json.dumps(_context_payload(context), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Human labels for each payload category, used to say WHAT triggered a PM cycle.
_TRIGGER_LABELS = {
    "goals": "goals edited",
    "guidance": "guidance changed",
    "active": "sprint approved / state change",
    "completed": "a result completed",
    "failed": "a sprint failed",
    "sprint_feedback": "feedback to the planner",
    "human_ideas": "a human idea",
    "idea_comments": "comment on an idea",
}


def context_signals(context: PMContext) -> dict:
    """A per-category signature so the next cycle can name what changed."""
    return {k: hashlib.sha1(json.dumps(v, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
            for k, v in _context_payload(context).items()}


def _triggers(last_signals: dict, new_signals: dict, forced: bool) -> list[str]:
    if not last_signals:
        return ["first cycle"]
    changed = [_TRIGGER_LABELS.get(k, k) for k, h in new_signals.items()
               if last_signals.get(k) != h]
    return changed or (["manual replan"] if forced else [])


def gather_context(substrate, program_id: str) -> PMContext:
    program = substrate.load_program(program_id)
    pm = substrate.load_pm_state(program_id)
    open_sprints: list[dict] = []
    completed: list[dict] = []
    failed: list[dict] = []
    sprint_feedback: list[dict] = []
    for s in substrate.iter_sprints():
        if s.program != program_id:
            continue
        pm_notes = [c["text"] for c in s.comments if c.get("target") == "pm"]
        if pm_notes and s.status != SprintStatus.CANCELED:
            sprint_feedback.append({
                "sprint_id": s.id, "goals": s.goals, "status": s.status.value,
                # PM may revise a sprint only until a human approves it; after that
                # the spec is locked and the PM responds by proposing a follow-up.
                "editable": s.status == SprintStatus.PROPOSED,
                "comments": pm_notes,
            })
        if s.status == SprintStatus.DONE:
            result = ""
            if s.results:
                try:
                    result = substrate.load_result(s.results[0]).summary
                except OSError:
                    result = ""
            completed.append({"id": s.id, "goals": s.goals, "result": result})
        elif s.status == SprintStatus.FAILED:
            err = substrate.load_progress(s.id).last_error
            failed.append({"id": s.id, "goals": s.goals, "error": err})
        elif s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED,
                          SprintStatus.QUEUED, SprintStatus.EXECUTING):
            open_sprints.append({"id": s.id, "status": s.status.value, "goals": s.goals})
    guidance = [n["text"] for n in substrate.load_guidance(program_id)]
    _summary, ideas = substrate.load_ideas(program_id)
    idea_dicts = [{"id": i.id, "text": i.text, "source": i.source,
                   "protected": i.protected, "demoted": i.demoted,
                   "comments": [c["text"] for c in i.comments]} for i in ideas]
    proposed_count = sum(1 for s in open_sprints if s["status"] == SprintStatus.PROPOSED.value)
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        open_sprints=open_sprints, completed=completed, failed=failed,
        sprint_feedback=sprint_feedback,
        prior_proposals=list(pm.proposed_ids),
        human_guidance=guidance,
        ideas=idea_dicts, proposed_count=proposed_count, max_proposed=MAX_PROPOSED,
        model=program.pm_model,
        workdir=_resolve_workdir(substrate, program.workdir),
    )


def _resolve_workdir(substrate, workdir: str) -> str:
    """The cwd the PM's headless claude session should run in: the program's
    project folder if it set one (and it exists on disk), else the control repo.
    Mirrors Worker._agent_cwd so the planner explores the same tree as its workers,
    instead of inheriting whatever directory the loop process was launched from."""
    if workdir:
        p = os.path.expanduser(workdir)
        if os.path.isdir(p):
            return p
    return str(substrate.repo_root)


@dataclass
class StagedCycle:
    cycle: int
    output: PMCycleOutput
    fingerprint: str = ""


def proposal_id(program_id: str, cycle: int, suffix: str) -> str:
    # The model sometimes returns a suffix that already carries the program and/or
    # cycle prefix (e.g. "c2-foo" or "p1-c3-bar"), which would otherwise produce
    # doubled ids like "p1-c2-c2-foo". Strip any such leading prefixes first.
    s = suffix.strip().strip("-/ ")
    prev = None
    while prev != s:
        prev = s
        s = re.sub(rf"^{re.escape(program_id)}-", "", s)
        s = re.sub(r"^c\d+-", "", s)
    return f"{program_id}-c{cycle}-{s}"


def _staging_path(substrate, program_id: str):
    return substrate.program_dir(program_id) / ".pm" / "cycle-staging.json"


def write_staging(substrate, program_id: str, cycle: int, output: PMCycleOutput,
                  fingerprint: str = "") -> None:
    path = _staging_path(substrate, program_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cycle": cycle,
        "fingerprint": fingerprint,
        "report": output.report,
        "ideas_summary": output.ideas_summary,
        "new_ideas": list(output.new_ideas),
        "delete_idea_ids": list(output.delete_idea_ids),
        "sprint_edits": list(output.sprint_edits),
        "proposals": [
            {"suffix": p.suffix, "goals": p.goals, "plan": p.plan,
             "priority": p.priority, "resources_required": p.resources_required,
             "rationale": p.rationale, "title": p.title, "summary": p.summary,
             "from_idea": p.from_idea, "model": p.model}
            for p in output.proposals
        ],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)  # atomic on POSIX


def read_staging(substrate, program_id: str) -> "StagedCycle | None":
    path = _staging_path(substrate, program_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    output = PMCycleOutput(
        report=data.get("report", ""),
        ideas_summary=data.get("ideas_summary", ""),
        new_ideas=list(data.get("new_ideas", [])),
        delete_idea_ids=list(data.get("delete_idea_ids", [])),
        sprint_edits=list(data.get("sprint_edits", [])),
        proposals=[ProposedSprint(**p) for p in data.get("proposals", [])],
    )
    return StagedCycle(cycle=int(data["cycle"]), output=output,
                       fingerprint=data.get("fingerprint", ""))


def clear_staging(substrate, program_id: str) -> None:
    path = _staging_path(substrate, program_id)
    if path.is_file():
        path.unlink()


def pm_beat(substrate, program_id: str, reasoner, now: float | None = None,
            usage_ok=None, force: bool = False) -> dict:
    """Run one bounded, kill-safe PM cycle for a program under a per-program lock.

    The lock serialises the background loop against on-demand "replan now" calls so
    they can never reason concurrently or race the staging commit. If another beat
    already holds it, this one returns a `busy` skip instead of blocking. `force`
    bypasses the event-gate (an explicit human replan reasons even if nothing changed)."""
    lock = _acquire_program_lock(substrate, program_id)
    if lock is None:
        pm = substrate.load_pm_state(program_id)
        return {"program": program_id, "cycle": pm.cycle,
                "submitted": [], "proposed": [], "skipped": True, "busy": True}
    try:
        return _run_pm_cycle(substrate, program_id, reasoner, now, usage_ok, force)
    finally:
        _release_program_lock(lock)


def _acquire_program_lock(substrate, program_id: str):
    """Non-blocking per-program advisory lock (flock). Returns the open file handle
    on success, or None if another process/thread already holds it. The OS releases
    it if the holder dies, preserving kill-safety."""
    lockdir = substrate.repo_root / ".coscience"
    lockdir.mkdir(parents=True, exist_ok=True)
    f = open(lockdir / f"pm-{program_id}.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _release_program_lock(f) -> None:
    try:
        fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


def _run_pm_cycle(substrate, program_id: str, reasoner, now: float | None = None,
                  usage_ok=None, force: bool = False) -> dict:
    """One PM cycle body (already holding the program lock): gather context, reason
    once behind the staging commit, then idempotently apply proposals/ideas/edits.

    `usage_ok` is an optional () -> bool gate (production passes the real Claude
    usage check). When it returns False we skip the reasoner call WITHOUT advancing
    the fingerprint, so the pending change is re-reasoned once the budget recovers."""
    pm = substrate.load_pm_state(program_id)

    new_signals = None       # set when we actually reason -> drives the activation record
    trigger_labels = None
    staged = read_staging(substrate, program_id)
    if staged is None:
        cycle = pm.cycle
        context = gather_context(substrate, program_id)
        fingerprint = context_fingerprint(context)
        if not force and fingerprint == pm.last_fingerprint:
            # Event-driven: nothing the PM acts on has changed since the last cycle
            # (no new results, guidance, approvals or goal edits). Stay idle — don't
            # burn a reasoner call or pile up redundant proposals.
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            return {"program": program_id, "cycle": cycle,
                    "submitted": [], "proposed": [], "skipped": True}
        if usage_ok is not None and not usage_ok():
            # Budget exhausted: do NOT call the reasoner (it would shell out to a dead
            # `claude` and raise). Leave the fingerprint pending; retry when it frees up.
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            return {"program": program_id, "cycle": cycle,
                    "submitted": [], "proposed": [], "skipped": True, "throttled": True}
        # About to reason -> capture what changed since the last reasoned cycle.
        new_signals = context_signals(context)
        trigger_labels = _triggers(pm.last_signals, new_signals, force)
        output = reasoner.run(context)                 # the ONE reasoner call
        lc = getattr(reasoner, "last_cost", None) or {}
        usage_meter.record_run(substrate.repo_root, "pm", program_id,
                               cost=lc.get("cost"), tokens=lc.get("tokens"),
                               model=context.model)
        write_staging(substrate, program_id, cycle, output, fingerprint)  # COMMIT POINT
        staged = StagedCycle(cycle=cycle, output=output, fingerprint=fingerprint)

    cycle = staged.cycle
    now_ts = time.time() if now is None else now
    summary_text, ideas = substrate.load_ideas(program_id)
    ideas_by_id = {i.id: i for i in ideas}

    submitted: list[str] = []
    proposed: list[str] = []
    dropped: list[str] = []
    # Free slots = the cap minus sprints already awaiting review. Enforced here so
    # the PM can never push past it, whatever the reasoner returns.
    open_proposed = sum(1 for s in substrate.iter_sprints(status=SprintStatus.PROPOSED)
                        if s.program == program_id)
    slots = MAX_PROPOSED - open_proposed
    for prop in staged.output.proposals:
        # A demoted idea is a human "do not pursue as a sprint" — the PM may not
        # promote it back, whatever the reasoner returns.
        if prop.from_idea:
            src = ideas_by_id.get(prop.from_idea)
            if src is not None and src.demoted:
                continue
        sid = proposal_id(program_id, cycle, prop.suffix)
        exists = (substrate.sprint_dir(sid) / "sprint.md").is_file()
        if not exists:
            if slots <= 0:
                dropped.append(sid)                    # over the cap -> not proposed
                continue
            substrate.save_sprint(Sprint(
                id=sid, status=SprintStatus.PROPOSED, goals=prop.goals,
                plan=list(prop.plan),
                program=program_id, priority=prop.priority,
                resources_required=coerce_resources(prop.resources_required),
                rationale=prop.rationale,
                title=prop.title,
                summary=prop.summary,
                model=prop.model,
            ))
            slots -= 1
        proposed.append(sid)
        if sid not in pm.proposed_ids:
            submitted.append(sid)                      # new this run
        # A promotion: the originating idea has become a sprint -> drop it from the pool.
        if prop.from_idea:
            ideas_by_id.pop(prop.from_idea, None)

    # --- idea pool: prune, add, and re-summarise (protection enforced here) ---
    for iid in staged.output.delete_idea_ids:
        target = ideas_by_id.get(iid)
        if target is not None and target.source == "pm" and not target.protected:
            del ideas_by_id[iid]
    existing_texts = {i.text for i in ideas_by_id.values()}
    for text in staged.output.new_ideas:
        text = str(text).strip()
        if not text or text in existing_texts:
            continue
        # deterministic id so re-applying a staged cycle doesn't duplicate ideas
        iid = hashlib.sha1(f"{program_id}|{cycle}|{text}".encode("utf-8")).hexdigest()[:8]
        if iid in ideas_by_id:
            continue
        ideas_by_id[iid] = Idea(id=iid, text=text, source="pm", created_at=now_ts)
        existing_texts.add(text)
    new_summary = staged.output.ideas_summary or summary_text
    substrate.save_ideas(program_id, new_summary, list(ideas_by_id.values()))

    # --- sprint revisions from PM-targeted feedback (only while still proposed) ---
    for edit in staged.output.sprint_edits:
        sid = str(edit.get("sprint_id", ""))
        if not sid or not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id or sp.status != SprintStatus.PROPOSED:
            continue                                   # locked once a human approves
        if edit.get("goals"):
            sp.goals = str(edit["goals"])
        if edit.get("plan") is not None:
            sp.plan = [str(x) for x in edit["plan"]]
        if edit.get("summary") is not None:
            sp.summary = str(edit["summary"])
        if edit.get("title") is not None:
            sp.title = str(edit["title"])
        if edit.get("priority") is not None:
            try:
                sp.priority = int(edit["priority"])
            except (TypeError, ValueError):
                pass
        substrate.save_sprint(sp)

    # --- reopen: pull an APPROVED sprint back to PROPOSED when results made it
    # obsolete. Guarded to approved sprints of this program only — the PM must not
    # touch queued/executing work (a human deliberately released those).
    reopened: list[str] = []
    for sid in staged.output.reopen_ids:
        sid = str(sid)
        if not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id or sp.status != SprintStatus.APPROVED:
            continue
        sp.status = SprintStatus.PROPOSED
        substrate.save_sprint(sp)
        reopened.append(sid)

    substrate.save_report(program_id, staged.output.report)

    pm.cycle = cycle + 1
    pm.last_run = now_ts
    pm.last_fingerprint = staged.fingerprint
    for sid in proposed:
        if sid not in pm.proposed_ids:
            pm.proposed_ids.append(sid)
    pm.log.append(f"cycle {cycle}: proposed {proposed}"
                  + (f", dropped {dropped} (cap)" if dropped else ""))
    if new_signals is not None:                        # we actually reasoned this beat
        pm.last_signals = new_signals
        pm.activations.append({
            "at": now_ts, "cycle": cycle, "triggers": trigger_labels,
            "submitted": list(submitted), "forced": bool(force),
        })
        pm.activations = pm.activations[-50:]          # keep the recent timeline bounded
    substrate.save_pm_state(pm)

    clear_staging(substrate, program_id)
    return {"program": program_id, "cycle": cycle, "submitted": submitted,
            "proposed": proposed, "dropped": dropped, "skipped": False}
