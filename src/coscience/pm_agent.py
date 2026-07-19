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

from coscience import graph, threads, usage_meter
from coscience.models import Sprint, SprintStatus, Idea, set_status
from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint, coerce_resources

# The PM may not push the program past this many sprints awaiting human review.
# Humans can propose beyond it; this only gates the PM's own proposing/promoting.
MAX_PROPOSED = 4

MAX_EDGE_OPS = 100   # bound the edges the PM may add per cycle (headroom for lineage back-fill)


def _context_payload(context: PMContext) -> dict:
    """The per-category inputs the PM reacts to. The PM's own pending proposals
    (status 'proposed') are deliberately excluded — they are its output, not new
    input, so proposing does not re-trigger the next cycle."""
    return {
        "goals": context.goals,
        # Keyed on (thread_id, last-human-text) — same shape as idea_comments below —
        # so a new guidance message re-triggers the PM even if other guidance is unchanged.
        "guidance": sorted((f["thread_id"], f["messages"][-1]["text"])
                           for f in context.guidance_feedback),
        "active": sorted((s["id"], s["status"]) for s in context.open_sprints
                         if s["status"] != SprintStatus.PROPOSED.value),
        "completed": sorted((s["id"], s["result"]) for s in context.completed),
        "failed": sorted((s["id"], s["error"]) for s in context.failed),
        "sprint_feedback": sorted((f["sprint_id"], f["thread_id"], f["messages"][-1]["text"])
                                  for f in context.sprint_feedback),
        # Human idea signal re-triggers the PM; its own pm-sourced ideas/summary do not.
        "human_ideas": sorted(i["text"] for i in context.ideas if i.get("source") == "human"),
        # A human message on an idea thread re-triggers the PM, same shape as
        # sprint_feedback above (idea_id instead of sprint_id).
        "idea_comments": sorted((f["idea_id"], f["thread_id"], f["messages"][-1]["text"])
                                for f in context.idea_feedback),
        "artifact_feedback": sorted((f["artifact_id"], f["thread_id"], f["messages"][-1]["text"])
                                    for f in context.artifact_feedback),
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
    "artifact_feedback": "comment on an artifact",
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
        for th in s.threads:
            if (th.get("target") == "pm" and threads.needs_reply(th)
                    and s.status not in (SprintStatus.CANCELED, SprintStatus.PARKED)):
                sprint_feedback.append({
                    "sprint_id": s.id, "goals": s.goals, "status": s.status.value,
                    # PM may revise a sprint while it is still proposed/approved/queued;
                    # once executing/done/failed the spec is locked and the PM should
                    # respond by proposing a follow-up instead.
                    "editable": s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED,
                                             SprintStatus.QUEUED),
                    "thread_id": th["id"],
                    "messages": [{"role": m["role"], "text": m["text"]} for m in th["messages"]],
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
                          SprintStatus.QUEUED, SprintStatus.EXECUTING,
                          SprintStatus.HIBERNATED):
            open_sprints.append({"id": s.id, "status": s.status.value, "goals": s.goals,
                                 "priority": s.priority})
    guidance_threads = substrate.load_guidance(program_id)
    # Standing guidance shown every cycle as background context (latest text per
    # thread, whether open or already addressed) plus the open threads the PM must
    # act on and reply to, same mechanism as idea_feedback below.
    guidance = [th["messages"][-1]["text"] for th in guidance_threads if th.get("messages")]
    guidance_feedback = [{"thread_id": th["id"],
                          "messages": [{"role": m["role"], "text": m["text"]} for m in th["messages"]]}
                         for th in guidance_threads if threads.needs_reply(th)]
    _summary, ideas = substrate.load_ideas(program_id)
    idea_dicts = [{"id": i.id, "text": i.text, "source": i.source,
                   "protected": i.protected, "demoted": i.demoted, "pinned": i.pinned} for i in ideas]
    idea_feedback: list[dict] = []
    for i in ideas:
        # Idea threads are always target "pm" — no worker runs against a pool idea.
        for th in i.threads:
            if threads.needs_reply(th):
                idea_feedback.append({
                    "idea_id": i.id, "thread_id": th["id"],
                    "messages": [{"role": m["role"], "text": m["text"]} for m in th["messages"]],
                })
    artifact_dicts: list[dict] = []
    artifact_feedback: list[dict] = []
    for art in substrate.iter_artifacts(program_id):
        artifact_dicts.append({"id": art.id, "title": art.title, "kind": art.kind})
        for th in art.threads:
            if threads.needs_reply(th):
                artifact_feedback.append({
                    "artifact_id": art.id, "thread_id": th["id"],
                    "messages": [{"role": m["role"], "text": m["text"]} for m in th["messages"]],
                })
    proposed_count = sum(1 for s in open_sprints if s["status"] == SprintStatus.PROPOSED.value)
    # Windowed lineage graph: adjacency for edges whose SOURCE is a node already
    # shown in this prompt (ideas + open/completed/failed sprints). Keeps the
    # block proportional to the rendered window, not the whole program.
    shown_ids = ({i.id for i in ideas}
                 | {s["id"] for s in open_sprints}
                 | {s["id"] for s in completed} | {s["id"] for s in failed})
    graph_lines: list[str] = []
    for s in substrate.iter_sprints():
        if s.program == program_id and s.id in shown_ids and s.edges:
            rel = "; ".join(f"{e['type']} {e['dst']}" for e in s.edges)
            graph_lines.append(f"{s.id}: {rel}")
    for i in ideas:
        if i.id in shown_ids and i.edges:
            rel = "; ".join(f"{e['type']} {e['dst']}" for e in i.edges)
            graph_lines.append(f"{i.id}: {rel}")
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        open_sprints=open_sprints, completed=completed, failed=failed,
        sprint_feedback=sprint_feedback,
        prior_proposals=list(pm.proposed_ids),
        human_guidance=guidance, guidance_feedback=guidance_feedback,
        ideas=idea_dicts, idea_feedback=idea_feedback,
        proposed_count=proposed_count, max_proposed=MAX_PROPOSED,
        model=program.pm_model,
        workdir=_resolve_workdir(substrate, program.workdir),
        graph_lines=graph_lines,
        artifacts=artifact_dicts, artifact_feedback=artifact_feedback,
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
    directive: str = ""       # "compress"/"brainstorm"/"" — carried so a resumed cycle applies the same rules


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
                  fingerprint: str = "", directive: str = "") -> None:
    path = _staging_path(substrate, program_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cycle": cycle,
        "fingerprint": fingerprint,
        "directive": directive,
        "report": output.report,
        "ideas_summary": output.ideas_summary,
        "new_ideas": list(output.new_ideas),
        "delete_idea_ids": list(output.delete_idea_ids),
        "idea_order": list(output.idea_order),
        "sprint_edits": list(output.sprint_edits),
        "reopen_ids": list(output.reopen_ids),
        "release_ids": list(output.release_ids),
        "thread_replies": list(output.thread_replies),
        "edge_ops": list(output.edge_ops),
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
        idea_order=list(data.get("idea_order", [])),
        sprint_edits=list(data.get("sprint_edits", [])),
        reopen_ids=list(data.get("reopen_ids", [])),
        release_ids=list(data.get("release_ids", [])),
        thread_replies=list(data.get("thread_replies", [])),
        edge_ops=list(data.get("edge_ops", [])),
        proposals=[ProposedSprint(**p) for p in data.get("proposals", [])],
    )
    return StagedCycle(cycle=int(data["cycle"]), output=output,
                       fingerprint=data.get("fingerprint", ""),
                       directive=data.get("directive", ""))


def clear_staging(substrate, program_id: str) -> None:
    path = _staging_path(substrate, program_id)
    if path.is_file():
        path.unlink()


def pm_beat(substrate, program_id: str, reasoner, now: float | None = None,
            usage_ok=None, force: bool = False, directive: str = "") -> dict:
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
        return _run_pm_cycle(substrate, program_id, reasoner, now, usage_ok, force, directive)
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


def _rewire_on_promote(substrate, program_id: str, old_idea_id: str,
                       new_sid: str, ideas_by_id: dict) -> None:
    """Move the promoted idea's edges (both directions) onto the new sprint.
    Inbound edges may live on other ideas or other sprints, so scan the whole
    program node set. The new sprint is saved on disk already; save every node
    the rewire touched. Pool ideas are saved by the caller."""
    program_sprints = [s for s in substrate.iter_sprints() if s.program == program_id]
    nodes = list(ideas_by_id.values()) + program_sprints
    changed = graph.repoint_edges(old_idea_id, new_sid, nodes)
    sprint_by_id = {s.id: s for s in program_sprints}
    for nid in changed:
        if nid in sprint_by_id:
            substrate.save_sprint(sprint_by_id[nid])


def _apply_edge_ops(substrate, program_id: str, ops: list[dict],
                    ideas_by_id: dict, now_ts: float) -> tuple[int, int]:
    """Apply the PM's edge diffs deterministically: validate each, silently drop
    invalid ones, dedup, cap adds, and forbid deleting non-PM edges. Returns
    (added, removed). Ideas are mutated in place (saved by the caller); changed
    sprints are saved here."""
    program_sprints = [s for s in substrate.iter_sprints() if s.program == program_id]
    nodes = list(ideas_by_id.values()) + program_sprints
    node_by_id = {n.id: n for n in nodes}
    sprint_ids = {s.id for s in program_sprints}
    existing = graph.all_edges(nodes)
    existing_ids = {e["id"] for e in existing}
    changed_sprint_ids: set[str] = set()
    added = removed = 0
    for op in ops:
        if not isinstance(op, dict):
            continue                                       # tolerate corrupted staging/LLM output
        kind = str(op.get("op", ""))
        # `or ""` (not a get-default) so an explicit JSON null normalizes to empty.
        etype = str(op.get("type") or "")
        src, dst = str(op.get("src") or ""), str(op.get("dst") or "")
        if kind == "add":
            if added >= MAX_EDGE_OPS:
                continue
            if not str(op.get("rationale") or "").strip():
                continue                                   # asserted adds must justify
            edge = graph.new_edge(
                etype, src, dst, "pm", by="pm", at=now_ts,
                rationale=str(op.get("rationale") or ""),
                confidence=str(op.get("confidence") or ""),
                evidence=str(op.get("evidence") or ""))
            if edge["id"] in existing_ids:
                continue                                   # dedup
            if graph.validate_edge(edge, nodes, existing) is not None:
                continue                                   # invalid -> drop
            node_by_id[src].edges.append(edge)
            existing.append(edge)
            existing_ids.add(edge["id"])
            if src in sprint_ids:
                changed_sprint_ids.add(src)
            added += 1
        elif kind == "delete":
            eid = graph.edge_id(etype, src, dst)
            holder = node_by_id.get(src)
            if holder is None:
                continue
            kept = [e for e in holder.edges
                    if not (e["id"] == eid and e.get("source") == "pm")]  # PM deletes only its own
            if len(kept) != len(holder.edges):
                holder.edges = kept
                existing_ids.discard(eid)
                existing[:] = [e for e in existing if e["id"] != eid]  # keep cycle-check view in sync
                if src in sprint_ids:
                    changed_sprint_ids.add(src)
                removed += 1
    sprint_by_id = {s.id: s for s in program_sprints}
    for sid in changed_sprint_ids:
        substrate.save_sprint(sprint_by_id[sid])
    return added, removed


def _run_pm_cycle(substrate, program_id: str, reasoner, now: float | None = None,
                  usage_ok=None, force: bool = False, directive: str = "") -> dict:
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
        context.directive = directive        # directed cycle (compress/brainstorm); "" = normal
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
        write_staging(substrate, program_id, cycle, output, fingerprint, directive)  # COMMIT POINT
        staged = StagedCycle(cycle=cycle, output=output, fingerprint=fingerprint, directive=directive)

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
        # A promotion: the originating idea has become a sprint. Move its edges
        # onto the sprint (both directions), then drop it from the pool.
        if prop.from_idea:
            if prop.from_idea in ideas_by_id:
                _rewire_on_promote(substrate, program_id, prop.from_idea, sid, ideas_by_id)
            ideas_by_id.pop(prop.from_idea, None)

    # --- idea pool: prune, add, re-rank, and re-summarise (protection enforced here) ---
    # Protection is pinned-only: the PM may prune ANY idea that is not pinned. Human,
    # commented, and demoted ideas are auto-pinned when created, so they're protected
    # until a human unpins them.
    ideas_removed = 0
    pruned_ids: list[str] = []
    for iid in staged.output.delete_idea_ids:
        target = ideas_by_id.get(iid)
        if target is None or target.pinned:
            continue
        del ideas_by_id[iid]
        pruned_ids.append(iid)
        ideas_removed += 1
    # Cascade: a pruned idea is deleted outright (not transitioned), so drop every
    # edge that pointed AT it, or a surviving node keeps a dangling reference.
    if pruned_ids:
        prog_sprints = [s for s in substrate.iter_sprints() if s.program == program_id]
        cascade_nodes = list(ideas_by_id.values()) + prog_sprints
        cascade_changed: set[str] = set()
        for did in pruned_ids:
            cascade_changed |= graph.drop_edges_to(did, cascade_nodes)
        sp_by_id = {s.id: s for s in prog_sprints}
        for nid in cascade_changed:
            if nid in sp_by_id:
                substrate.save_sprint(sp_by_id[nid])   # idea-side saved by save_ideas below
    existing_texts = {i.text for i in ideas_by_id.values()}
    ideas_added = 0
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
        ideas_added += 1
    # Re-rank the pool if the reasoner returned an ordering (compress): ids it lists
    # first (in that order), then any it omitted, in their existing order.
    if staged.output.idea_order:
        ordered = {iid: ideas_by_id[iid] for iid in staged.output.idea_order if iid in ideas_by_id}
        for iid, idea in ideas_by_id.items():
            ordered.setdefault(iid, idea)
        ideas_by_id = ordered
    edges_added, edges_removed = _apply_edge_ops(
        substrate, program_id, staged.output.edge_ops, ideas_by_id, now_ts)
    new_summary = staged.output.ideas_summary or summary_text
    substrate.save_ideas(program_id, new_summary, list(ideas_by_id.values()))

    # --- sprint revisions from PM-targeted feedback. Goals/plan/title/summary are
    # editable only while proposed (locked once a human approves); priority the PM
    # may retune on the approved queue and the run-queue too, so it can order what
    # runs next. ---
    _EDITABLE = (SprintStatus.PROPOSED, SprintStatus.APPROVED, SprintStatus.QUEUED)
    for edit in staged.output.sprint_edits:
        sid = str(edit.get("sprint_id", ""))
        if not sid or not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id or sp.status not in _EDITABLE:
            continue
        if sp.status == SprintStatus.PROPOSED:
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
        # Only a real dict applies ({} clears); a malformed non-dict value from the
        # LLM is ignored (leaves compute unchanged) rather than wiping it.
        if isinstance(edit.get("resources_required"), dict):
            sp.resources_required = coerce_resources(edit["resources_required"])
        substrate.save_sprint(sp)

    # --- thread replies: the PM's answer to each open feedback thread it acted on
    # (edited, released, proposed a follow-up, or explained why not). One reply
    # map, applied across every surface a thread id can belong to — sprints,
    # then pool ideas, then standing guidance — since the LLM doesn't say which
    # one it's answering. Appended as a 'pm' message on the matching still-open
    # thread. ---
    # Guard both keys — the LLM may omit `text`; skip such entries rather than
    # KeyError-crashing the whole PM tick (which loops over every active program).
    replies = {r["thread_id"]: str(r.get("text") or "")
               for r in staged.output.thread_replies
               if r.get("thread_id") and r.get("text")}
    if replies:
        for s in substrate.iter_sprints():
            if s.program != program_id:
                continue
            touched = False
            for th in s.threads:
                if th["id"] in replies and threads.needs_reply(th):
                    threads.append(th, "pm", replies[th["id"]], "", now=now_ts)
                    touched = True
            if touched:
                substrate.save_sprint(s)

        touched_ideas = False
        for idea in ideas_by_id.values():
            for th in idea.threads:
                if th["id"] in replies and threads.needs_reply(th):
                    threads.append(th, "pm", replies[th["id"]], "", now=now_ts)
                    touched_ideas = True
        if touched_ideas:
            substrate.save_ideas(program_id, new_summary, list(ideas_by_id.values()))

        touched_guidance = False
        guidance_threads = substrate.load_guidance(program_id)
        for th in guidance_threads:
            if th["id"] in replies and threads.needs_reply(th):
                threads.append(th, "pm", replies[th["id"]], "", now=now_ts)
                touched_guidance = True
        if touched_guidance:
            substrate.save_guidance(program_id, guidance_threads)

    # --- release: put an APPROVED sprint into production (-> queued). The approved
    # pool is the PM's managed queue; it releases items here as it sees need, and the
    # dispatcher runs queued sprints by priority as compute frees. Guarded to this
    # program's approved sprints. ---
    released: list[str] = []
    for sid in staged.output.release_ids:
        sid = str(sid)
        if not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id or sp.status != SprintStatus.APPROVED:
            continue
        set_status(sp, SprintStatus.QUEUED, by="pm", action="run")
        substrate.save_sprint(sp)
        released.append(sid)

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
        set_status(sp, SprintStatus.PROPOSED, by="pm", action="reopen")
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
            "proposed": proposed, "dropped": dropped, "skipped": False,
            "ideas_added": ideas_added, "ideas_removed": ideas_removed,
            "pool_size": len(ideas_by_id),
            "edges_added": edges_added, "edges_removed": edges_removed}
