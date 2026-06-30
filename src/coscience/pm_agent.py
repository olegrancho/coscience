"""The PM heartbeat: gather context, call the reasoner once (fenced behind an
atomic staging commit), then idempotently submit proposed sprints + write the
report. Deterministic and kill-safe; the reasoner does no writes."""
from __future__ import annotations

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


def context_fingerprint(context: PMContext) -> str:
    """A stable hash of the inputs the PM should react to: program goals, human
    guidance, the state of approved/running/done work, and results. The PM's own
    pending proposals (status 'proposed') are deliberately excluded — they are its
    output, not new input, so proposing does not re-trigger the next cycle."""
    payload = {
        "goals": context.goals,
        "guidance": sorted(context.human_guidance),
        "active": sorted((s["id"], s["status"]) for s in context.open_sprints
                         if s["status"] != SprintStatus.PROPOSED.value),
        "completed": sorted((s["id"], s["result"]) for s in context.completed),
        # Human idea signal re-triggers the PM; its own pm-sourced ideas/summary do not.
        "human_ideas": sorted(i["text"] for i in context.ideas if i.get("source") == "human"),
        "idea_comments": sorted(c["text"] for i in context.ideas for c in i.get("comments", [])),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def gather_context(substrate, program_id: str) -> PMContext:
    program = substrate.load_program(program_id)
    pm = substrate.load_pm_state(program_id)
    open_sprints: list[dict] = []
    completed: list[dict] = []
    for s in substrate.iter_sprints():
        if s.program != program_id:
            continue
        if s.status == SprintStatus.DONE:
            result = ""
            if s.results:
                try:
                    result = substrate.load_result(s.results[0]).summary
                except OSError:
                    result = ""
            completed.append({"id": s.id, "goals": s.goals, "result": result})
        elif s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED,
                          SprintStatus.EXECUTING):
            open_sprints.append({"id": s.id, "status": s.status.value, "goals": s.goals})
    guidance = [n["text"] for n in substrate.load_guidance(program_id)]
    _summary, ideas = substrate.load_ideas(program_id)
    idea_dicts = [{"id": i.id, "text": i.text, "source": i.source,
                   "protected": i.protected,
                   "comments": [c["text"] for c in i.comments]} for i in ideas]
    proposed_count = sum(1 for s in open_sprints if s["status"] == SprintStatus.PROPOSED.value)
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        open_sprints=open_sprints, completed=completed,
        prior_proposals=list(pm.proposed_ids),
        human_guidance=guidance,
        ideas=idea_dicts, proposed_count=proposed_count, max_proposed=MAX_PROPOSED,
    )


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
        "proposals": [
            {"suffix": p.suffix, "goals": p.goals, "plan": p.plan,
             "priority": p.priority, "resources_required": p.resources_required,
             "rationale": p.rationale, "title": p.title, "summary": p.summary,
             "from_idea": p.from_idea}
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
        proposals=[ProposedSprint(**p) for p in data.get("proposals", [])],
    )
    return StagedCycle(cycle=int(data["cycle"]), output=output,
                       fingerprint=data.get("fingerprint", ""))


def clear_staging(substrate, program_id: str) -> None:
    path = _staging_path(substrate, program_id)
    if path.is_file():
        path.unlink()


def pm_beat(substrate, program_id: str, reasoner, now: float | None = None) -> dict:
    """Run one bounded, kill-safe PM cycle for a program. Returns a summary."""
    pm = substrate.load_pm_state(program_id)

    staged = read_staging(substrate, program_id)
    if staged is None:
        cycle = pm.cycle
        context = gather_context(substrate, program_id)
        fingerprint = context_fingerprint(context)
        if fingerprint == pm.last_fingerprint:
            # Event-driven: nothing the PM acts on has changed since the last cycle
            # (no new results, guidance, approvals or goal edits). Stay idle — don't
            # burn a reasoner call or pile up redundant proposals.
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            return {"program": program_id, "cycle": cycle,
                    "submitted": [], "proposed": [], "skipped": True}
        output = reasoner.run(context)                 # the ONE reasoner call
        usage_meter.record_run(substrate.repo_root, "pm", program_id)
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

    substrate.save_report(program_id, staged.output.report)

    pm.cycle = cycle + 1
    pm.last_run = now_ts
    pm.last_fingerprint = staged.fingerprint
    for sid in proposed:
        if sid not in pm.proposed_ids:
            pm.proposed_ids.append(sid)
    pm.log.append(f"cycle {cycle}: proposed {proposed}"
                  + (f", dropped {dropped} (cap)" if dropped else ""))
    substrate.save_pm_state(pm)

    clear_staging(substrate, program_id)
    return {"program": program_id, "cycle": cycle, "submitted": submitted,
            "proposed": proposed, "dropped": dropped, "skipped": False}
