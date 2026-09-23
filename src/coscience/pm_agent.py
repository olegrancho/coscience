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
from dataclasses import dataclass, field, replace

from coscience import artifacts, escalation, graph, housekeeping, threads, usage_meter
from coscience.models import Sprint, SprintStatus, Idea, set_status
from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint, coerce_resources
# A module-level function, not a Substrate method: the host-notes apply below validates
# a name the reasoner wrote before it decides whether to touch the substrate at all.
from coscience.substrate import check_host_name

# The PM may not push the program past this many sprints awaiting human review.
# Humans can propose beyond it; this only gates the PM's own proposing/promoting.
MAX_PROPOSED = 4

# Attempts against one unchanged context before the PM stands down. Bounded, not
# zero: a flaky call deserves a retry, a deterministic one does not deserve 720
# per hour.
FAILURE_BACKOFF = 3


def program_cap(program) -> int:
    """How many sprints may await review for this program: its own setting, or the
    global default when unset. One helper so the prompt's number and the number the
    apply path enforces can never drift apart."""
    return program.max_proposed or MAX_PROPOSED


HOLD_REASON_MAX = 400   # hard cap, after the one-sentence trim below


def _proposed_by_pm(sprint: Sprint) -> Sprint:
    """Stamp a PM proposal as the PM's. save_sprint backfills an anonymous history
    entry when there is none, which made a PM proposal indistinguishable from a human
    writing one in the dashboard — and the program page highlights only what the
    viewer did not do, so a new proposal has to say whose it is (P3)."""
    set_status(sprint, sprint.status, by="pm", action="propose")
    return sprint


def hold_reason(text: str) -> str:
    """The rationale for keeping a sprint held, trimmed to its FIRST SENTENCE.

    One sentence is the whole point: it is read at a glance on the sprint and beside
    every other open sprint in the planner's own context, and a paragraph there stops
    being read at all. Enforced here rather than asked for in the prompt, because a
    prompt instruction is a request and this is a guarantee."""
    text = " ".join((text or "").split())
    # A sentence ends at .!? followed by a capital, or at the end of the text. Requiring
    # the capital is what keeps "Lead Finder (i.e. the baseline) has not reported" whole
    # — a bare "[.!?]\s" cut it at "i.e.".
    m = re.search(r"[.!?](?:\s+(?=[A-Z])|\s*$)", text)
    if m:
        text = text[:m.start() + 1]
    return text[:HOLD_REASON_MAX].rstrip()

MAX_EDGE_OPS = 100   # bound the edges the PM may add per cycle (headroom for lineage back-fill)

# A cycle once wrote a report saying it had released a sprint, pruned the idea pool and
# adopted an artifact while every action list came back empty — nothing happened, and no
# channel showed it. `report` is free prose the machinery never parses, so these two
# helpers close the gap: the ledger states what was ACTUALLY applied, and the claim check
# flags prose that describes an action the cycle did not submit.
#
# A claim is the planner saying it DID something, so the verb has to be in the PAST
# tense. The stem match ("releas(e|ed|es|ing)") swallowed every report explaining why
# it had NOT acted — "nothing to release", "I will release them once approved", "no
# approved sprints exist to release" — and the plural nouns too ("no prunes this
# cycle"). None of those is "released". Past tense alone, with the negation check
# below, cleared all seven live reports; before it, 18 of 29 recorded cycles carried a
# warning and every one that could still be read was wrong.
#
# Deliberately NOT tied to a first-person subject: the hallucination this exists to
# catch was subjectless ("Manuscript-draft released into production.").
_CLAIM_CHECKS = (
    # (what the prose claims, pattern, the summary key that would back it up)
    ("released an approved sprint", r"\breleased\b", "released"),
    ("pruned the idea pool", r"\bpruned\b", "ideas_removed"),
    # "adopted by" is a passive agent naming who does the adopting in general —
    # "a PNG adopted by the PM arrives byte-identical" describes the dedup rule,
    # not something this cycle did.
    ("adopted an artifact", r"\badopted\b(?!\s+by\b)", "adopted"),
    # A report may narrate answering an escalation (resume/reallocate/to_human)
    # without the cycle ever having submitted one in escalation_answers. Tied to
    # escalation context (not bare "resume"/"reallocate", which show up in plenty
    # of ordinary prose — "the job will resume", "nothing to reallocate") so this
    # only fires on an actual claim of answering one:
    #  - resumed/reallocated an ESCALATION: "resumed"/"reallocated" sharing a
    #    clause with "escalat..." (raised/escalated/still escalated, etc.);
    #  - handed one to a human: passed/handed/sent/routed/moved ... to (a) human.
    ("resumed, reallocated, or passed an escalation to a human",
     r"\b(?:resumed|reallocated)\b[^.]{0,60}\bescalat"
     r"|\b(?:passed|handed|sent|routed|moved)\b[^.]{0,40}\bto(?:\s+a)?[\s_]+human\b",
     "escalations_answered"),
)

# Negation words that, ANYWHERE in the claim's own sentence, mean the report is
# DENYING the action rather than claiming it. "I did not resume p1-s3" negates
# ahead of the verb; "I proposed and released nothing" and "I added two ideas and
# pruned none" negate behind it, which the earlier look-back could not see.
_NEGATION_RE = re.compile(r"\b(?:not|n't|never|no|none|nothing|neither|nor|without)\b", re.I)


def _sentence_around(text: str, start: int, end: int) -> str:
    """The whole sentence the match sits in — both sides of it. A denial can land
    either way round, so looking only backwards misses half of them."""
    left = max((text.rfind(c, 0, start) for c in ".!?\n"), default=-1)
    right = min((r for r in (text.find(c, end) for c in ".!?\n") if r != -1),
                default=len(text))
    return text[left + 1:right]


def _unnegated_match(pattern: str, text: str) -> bool:
    for m in re.finditer(pattern, text, re.I | re.M):
        if not _NEGATION_RE.search(_sentence_around(text, m.start(), m.end())):
            return True
    return False


# How much of a cycle's report one sprint keeps, and how many cycles it keeps.
SPRINT_NOTE_CHARS = 600
SPRINT_NOTES_KEPT = 10


def sprint_share(report: str, sprint_id: str) -> str:
    """The part of a cycle's report that is about this sprint (E2).

    The planner writes one report for the whole program, and `report.md` is overwritten
    by the next cycle — so a sprint that was released, held or dropped ends up carrying a
    status change with no surviving explanation. Every sentence that names the sprint is
    its share: crude, but it is the planner's own words rather than a summary of them,
    and a sprint the report never mentions keeps nothing rather than an invented reason.
    """
    text = " ".join((report or "").split())
    if not text or not sprint_id:
        return ""
    found: list[str] = []
    for m in re.finditer(re.escape(sprint_id), text):
        # A longer id that merely starts with this one is a different sprint.
        after = text[m.end():m.end() + 1]
        if after and (after.isalnum() or after in "-_"):
            continue
        sentence = _sentence_around(text, m.start(), m.end()).strip()
        if sentence and sentence not in found:
            found.append(sentence)
    out = " ".join(found)
    return out[:SPRINT_NOTE_CHARS].rstrip()


def touched_sprints(actions: dict) -> list[str]:
    """Every sprint this cycle acted on, in a stable order. A skipped action counts:
    the sprint the planner tried and failed to move is exactly the one whose record
    needs to say why something was attempted."""
    ids: list[str] = []
    for key in ("released", "held", "submitted", "dropped", "adopted",
                "escalations_answered",
                "release_skipped", "hold_skipped", "adopt_skipped", "escalation_skipped"):
        for item in actions.get(key) or ():
            ids.append(_action_id(item))
    return [sid for i, sid in enumerate(ids) if sid and sid not in ids[:i]]


def _action_id(item) -> str:
    """The sprint id out of one entry of an actions list. The lists do not agree on
    shape — applied ones hold bare ids, skipped ones {id, why}, and an answered
    escalation a (id, action) pair — and a new shape must not take down the cycle that
    is only trying to annotate it."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("id") or "")
    if isinstance(item, (tuple, list)) and item:
        return str(item[0])
    return ""


def record_sprint_notes(substrate, report: str, actions: dict, cycle: int,
                        at: float) -> list[str]:
    """Put each touched sprint's share of the cycle's reasoning on that sprint (E2).

    Only sprints the cycle actually acted on, and only when the report says something
    about them: a note that repeats what the status already shows is worse than none.
    Returns the ids that gained a note."""
    written: list[str] = []
    for sid in touched_sprints(actions):
        text = sprint_share(report, sid)
        if not text:
            continue
        try:
            sp = substrate.load_sprint(sid)
        except Exception:
            continue                      # dropped, renamed, or never written
        notes = list(sp.pm_notes or [])
        if notes and notes[-1].get("cycle") == cycle:
            continue                      # one note per sprint per cycle
        notes.append({"cycle": cycle, "at": at, "text": text})
        sp.pm_notes = notes[-SPRINT_NOTES_KEPT:]
        substrate.save_sprint(sp)
        written.append(sid)
    return written


def unbacked_claims(report: str, actions: dict) -> list[str]:
    """Actions the report text describes but the cycle never submitted.

    Deliberately a heuristic on the prose, so it only ever WARNS — a false positive
    must not cost a cycle. `actions` maps the summary key to what was applied."""
    flagged = []
    report = report or ""
    for label, pattern, key in _CLAIM_CHECKS:
        applied = actions.get(key)
        count = applied if isinstance(applied, int) else len(applied or ())
        if not count and _unnegated_match(pattern, report):
            flagged.append(label)
    return flagged


def actions_ledger(actions: dict) -> str:
    """A markdown block stating what the platform actually did this cycle.

    Appended under the reasoner's report so a human reading the dashboard sees the
    applied actions next to the narrative about them. Machine-written from the
    post-apply results, so unlike the report it cannot claim something that didn't
    happen."""
    def _ids(key):
        return ", ".join(str(i) for i in actions.get(key) or ())

    lines = []
    for label, key in (("Released", "released"), ("Held back", "held"),
                       ("Proposed", "submitted"), ("Adopted", "adopted")):
        if actions.get(key):
            lines.append(f"- {label}: {_ids(key)}")
    if actions.get("dropped"):
        lines.append(f"- Not proposed (over the cap): {_ids('dropped')}")
    for key, label in (("ideas_added", "Ideas added"), ("ideas_removed", "Ideas pruned")):
        if actions.get(key):
            lines.append(f"- {label}: {actions[key]}")
    if actions.get("host_notes_updated"):
        lines.append(f"- Host notes updated: {_ids('host_notes_updated')}")
    for sid, action in actions.get("escalations_answered") or ():
        lines.append(f"- Escalation answered: `{sid}` ({action})")
    for key, label in (("release_skipped", "Release FAILED"), ("hold_skipped", "Hold FAILED"),
                       ("adopt_skipped", "Adopt FAILED"),
                       ("escalation_skipped", "Escalation answer FAILED"),
                       ("host_note_skipped", "Host note FAILED")):
        for skip in actions.get(key) or ():
            lines.append(f"- {label}: `{skip['id']}` — {skip['why']}")
    for claim in actions.get("unbacked_claims") or ():
        lines.append(f"- ⚠️ the report above says it {claim}, but no such action was submitted")
    if not lines:
        lines.append("- No actions submitted this cycle.")
    return ("\n\n---\n\n**Actions this cycle** (recorded by the platform, not the planner)\n\n"
            + "\n".join(lines) + "\n")


def _context_payload(context: PMContext) -> dict:
    """The per-category inputs the PM reacts to. The PM's own pending proposals
    (status 'proposed') are deliberately excluded — they are its output, not new
    input, so proposing does not re-trigger the next cycle."""
    payload = {
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
    # Only present once a human writes instructions. An unconditional key would change
    # every program's fingerprint the moment this code ships, waking each of them for a
    # "change" nobody made — the payload is a wire format, not just a local dict.
    if context.instructions:
        payload["instructions"] = context.instructions
    # Same reasoning: a program with no escalations must not gain a new fingerprint
    # key, or every existing program wakes once this ships. Keyed on (sprint_id,
    # thread_id) — the fields inside an escalation are static once raised, so the
    # PM re-reasons the moment one appears, not on every unrelated detail.
    if context.escalations:
        payload["escalations"] = sorted((e["sprint_id"], e["thread_id"]) for e in context.escalations)
    # Same reasoning again, plus one of its own: the REPORTS' ids, never the notes'
    # texts. A new report is news the PM must react to; the note it then writes is its
    # own output, and keying on the text would have every fold-in wake the next cycle.
    if context.host_reports:
        payload["host_reports"] = sorted(str(r.get("id") or "") for r in context.host_reports)
    return payload


def context_fingerprint(context: PMContext) -> str:
    """A stable hash over all the inputs — unchanged hash means nothing to react to."""
    blob = json.dumps(_context_payload(context), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Human labels for each payload category, used to say WHAT triggered a PM cycle.
_TRIGGER_LABELS = {
    "goals": "goals edited",
    "instructions": "instructions edited",
    "guidance": "guidance changed",
    "active": "sprint approved / state change",
    "completed": "a result completed",
    "failed": "a sprint failed",
    "sprint_feedback": "feedback to the planner",
    "human_ideas": "a human idea",
    "idea_comments": "comment on an idea",
    "artifact_feedback": "comment on an artifact",
    "escalations": "sprint escalated",
    "host_reports": "a sprint reported on a host",
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


def _finished_at(sprint) -> float:
    """When this sprint reached its terminal state. `set_status` stamps every
    transition, so the last entry is the finish; 0.0 for records written before
    status history existed, which sorts them oldest."""
    hist = sprint.status_history or []
    if not hist:
        return 0.0
    try:
        return float(hist[-1].get("at") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def gather_context(substrate, program_id: str) -> PMContext:
    program = substrate.load_program(program_id)
    pm = substrate.load_pm_state(program_id)
    open_sprints: list[dict] = []
    completed: list[dict] = []
    failed: list[dict] = []
    sprint_feedback: list[dict] = []
    # Walk the sprint tree ONCE. Every pass re-reads and re-parses every sprint.md in
    # the substrate, and this runs on every idle beat of the PM loop — the lineage
    # block below used to trigger a second full walk for the same program.
    program_sprints = [s for s in substrate.iter_sprints() if s.program == program_id]
    escalations: list[dict] = []
    for s in program_sprints:
        for th in s.threads:
            if th.get("kind") == "escalation":
                # Escalations are surfaced separately (below) as `escalations`, with
                # their own PM-facing shape and rules — never as ordinary feedback.
                continue
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
            result = result_id = ""
            if s.results:
                try:
                    result = substrate.load_result(s.results[0]).summary
                    result_id = s.results[0]
                except OSError:
                    result = ""
            completed.append({"id": s.id, "goals": s.goals, "result": result,
                              # The id the prompt turns into a readable path for the
                              # clipped excerpt. Not a fingerprint input (see
                              # _context_payload, which reads id + result only).
                              "result_id": result_id,
                              "title": s.title, "finished_at": _finished_at(s)})
        elif s.status == SprintStatus.FAILED:
            err = substrate.load_progress(s.id).last_error
            failed.append({"id": s.id, "goals": s.goals, "error": err,
                           "title": s.title, "finished_at": _finished_at(s)})
        elif s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED,
                          SprintStatus.QUEUED, SprintStatus.EXECUTING,
                          SprintStatus.HIBERNATED):
            open_sprints.append({"id": s.id, "status": s.status.value, "goals": s.goals,
                                 "priority": s.priority,
                                 # A hold the PM itself set last cycle. Without it the
                                 # planner cannot tell a sprint it is already waiting on
                                 # from one it has never considered — so it would either
                                 # re-hold blindly or forget and release early, and a
                                 # human clearing a hold would be invisible to it.
                                 "hold": str((s.hold or {}).get("why") or "")})
        elif s.status == SprintStatus.ESCALATED:
            # A human-level escalation is not the PM's to answer (see
            # docs/sprint-lifecycle.md) — only "pm" ones reach its context.
            progress = substrate.load_progress(s.id)
            esc = progress.escalation or {}
            # A pending human stop (Fix D) already refuses every PM answer — showing
            # it here would just have the PM's answer bounce off "a stop is already
            # pending for this sprint" every cycle until the dispatcher carries the
            # stop out.
            if esc.get("level") == "pm" and not progress.stop_requested:
                from coscience.resources import load_pool
                hosts_allowed = escalation.move_targets(
                    load_pool(substrate.repo_root), program_id, progress.host, substrate.repo_root)
                escalations.append({
                    "sprint_id": s.id, "title": s.title,
                    "thread_id": str(esc.get("thread_id") or ""),
                    "by": str(esc.get("by") or ""), "host": str(esc.get("host") or ""),
                    "what": str(esc.get("what") or ""), "tried": str(esc.get("tried") or ""),
                    "may_have_broken_something": bool(esc.get("may_have_broken_something")),
                    "needs": str(esc.get("needs") or ""),
                    "hosts_allowed": hosts_allowed,
                })
    # Oldest first, so "the most recent N" is expressible when the prompt is rendered.
    completed.sort(key=lambda s: s["finished_at"])
    failed.sort(key=lambda s: s["finished_at"])
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
    for s in program_sprints:
        if s.id in shown_ids and s.edges:
            rel = "; ".join(f"{e['type']} {e['dst']}" for e in s.edges)
            graph_lines.append(f"{s.id}: {rel}")
    for i in ideas:
        if i.id in shown_ids and i.edges:
            rel = "; ".join(f"{e['type']} {e['dst']}" for e in i.edges)
            graph_lines.append(f"{i.id}: {rel}")
    capacity, leased, hosts = _compute(substrate, program_id)
    host_notes, host_reports = _usable_host_notes(substrate, program_id)
    return PMContext(
        program_id=program_id, goals=program.goals, cycle=pm.cycle,
        instructions=substrate.load_instructions(program_id),
        open_sprints=open_sprints, completed=completed, failed=failed,
        sprint_feedback=sprint_feedback,
        prior_proposals=list(pm.proposed_ids),
        human_guidance=guidance, guidance_feedback=guidance_feedback,
        ideas=idea_dicts, idea_feedback=idea_feedback,
        proposed_count=proposed_count, max_proposed=program_cap(program),
        model=program.pm_model,
        workdir=_resolve_workdir(substrate, program.workdir),
        results_dir=str(substrate.repo_root / "results"),
        graph_lines=graph_lines,
        artifacts=artifact_dicts, artifact_feedback=artifact_feedback,
        compute_capacity=capacity, compute_leased=leased, compute_hosts=hosts,
        escalations=escalations,
        host_notes=host_notes,
        host_reports=host_reports,
    )


def _usable_host_notes(substrate, program_id: str) -> tuple[dict[str, str], list[dict]]:
    """The notes and pending reports on servers this program may still use (O22).

    The apply refuses a note on any other server, so showing the planner one only asked
    it to fold in something it can never write: it tried every cycle, was refused every
    cycle, and the report carried the same skip line for good. Those stay on the program
    page, where a human can still read and clear them."""
    from coscience.resources import load_pool
    pool = load_pool(substrate.repo_root)

    def usable(host: str) -> bool:
        h = pool.host(host)
        return h is not None and h.allows(program_id)

    notes = {h: t for h, t in substrate.list_host_notes(program_id).items() if usable(h)}
    reports = [r for r in substrate.load_host_reports(program_id)
               if usable(str(r.get("host") or ""))]
    return notes, reports


def _compute(substrate, program_id: str) -> tuple[dict, dict, list[dict]]:
    """(capacity, currently leased, per-host view) of what a sprint in this program can
    request — only the hosts it may be placed on, so a reserved machine is never
    planned around by a program that will not get it. The per-host view is what the PM
    sizes against: a request must fit on one host."""
    from coscience import host_health
    from coscience.ledger import Ledger
    from coscience.resources import GPU_KEY, PLATFORM_KEYS, load_pool
    pool = load_pool(substrate.repo_root)
    health = host_health.load(substrate.repo_root)
    now = time.time()
    hosts = pool.placeable_hosts(program_id)
    capacity: dict[str, float] = {}
    for h in hosts:
        for k, v in h.capacity.items():
            capacity[k] = capacity.get(k, 0.0) + v
    try:
        ledger = Ledger(pool, substrate.repo_root / ".coscience" / "leases.json")
        ledger.load()
    except (OSError, ValueError, TypeError, KeyError):
        ledger = None
    leased: dict[str, float] = {}
    per_host: list[dict] = []
    for h in hosts:
        held = ({k: v for k, v in ledger.used(h.name).items() if k not in PLATFORM_KEYS and v}
                if ledger is not None else {})
        for k, v in held.items():
            leased[k] = leased.get(k, 0.0) + v
        closed = ("being removed" if h.removing else
                  ("draining" if h.drain else
                   ("not answering" if host_health.state(health.get(h.name), now) == "quiet" else "")))
        per_host.append({"name": h.name,
                         "capacity": {k: v for k, v in h.capacity.items() if k != GPU_KEY},
                         "gpus": [g.vram_gb for g in h.gpus],
                         "held": held,
                         "closed": closed})
    leased = {k: v for k, v in leased.items() if k in capacity and v}
    return capacity, leased, per_host


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
    # {server: [report ids]} the cycle was shown. The apply clears exactly these, so a
    # report filed while the reasoner ran (minutes) is not thrown away unread — and a
    # staged cycle re-applied after a restart still only clears what it saw.
    host_report_ids: dict = field(default_factory=dict)
    # The fingerprint this context WILL have once those reports are folded in. Stored
    # instead of the pre-apply one when the fold-in happens as staged, so the planner
    # wakes once for a report rather than twice (the second time for its own work).
    fingerprint_after: str = ""
    # {server: note text} as the cycle was shown it (O22). A note that has changed by
    # the time the cycle applies was saved by a human while the reasoner ran, and the
    # planner's rewrite would silently discard it. None for a cycle staged before this
    # was recorded, which applies as it always did.
    host_notes_seen: dict | None = None


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


def _artifact_slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    return s or "artifact"


def _artifact_specs(bound_ids, create_list) -> tuple[list[str], list[dict]]:
    """Normalise a proposal's/edit's artifact fields into (bound, create):
    `bound` = existing artifact ids to edit; `create` = new-artifact dicts with a
    slugged aid. Shared by artifact_tasks and sprint_edits so both bind the same way."""
    bound = [str(a) for a in (bound_ids or []) if str(a).strip()]
    create = []
    for c in (create_list or []):
        if isinstance(c, dict) and str(c.get("title") or "").strip():
            title = str(c["title"])
            create.append({"aid": _artifact_slug(title), "title": title,
                           "kind": str(c.get("kind") or "md")})
    return bound, create


def _staging_path(substrate, program_id: str):
    return substrate.program_dir(program_id) / ".pm" / "cycle-staging.json"


def write_staging(substrate, program_id: str, cycle: int, output: PMCycleOutput,
                  fingerprint: str = "", directive: str = "",
                  host_report_ids: dict | None = None, fingerprint_after: str = "",
                  host_notes_seen: dict | None = None) -> None:
    path = _staging_path(substrate, program_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cycle": cycle,
        "fingerprint": fingerprint,
        "fingerprint_after": fingerprint_after,
        "host_report_ids": dict(host_report_ids or {}),
        "host_notes_seen": host_notes_seen,
        "directive": directive,
        "report": output.report,
        "ideas_summary": output.ideas_summary,
        "new_ideas": list(output.new_ideas),
        "delete_idea_ids": list(output.delete_idea_ids),
        "idea_order": list(output.idea_order),
        "sprint_edits": list(output.sprint_edits),
        "holds": list(output.holds),
        "release_ids": list(output.release_ids),
        "thread_replies": list(output.thread_replies),
        "escalation_answers": list(output.escalation_answers),
        "host_notes": list(output.host_notes),
        "edge_ops": list(output.edge_ops),
        "artifact_tasks": list(output.artifact_tasks),
        "adopt_artifacts": list(output.adopt_artifacts),
        "proposals": [
            {"suffix": p.suffix, "goals": p.goals, "plan": p.plan,
             "priority": p.priority, "resources_required": p.resources_required,
             "distributed": p.distributed,
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
        holds=list(data.get("holds", [])),
        release_ids=list(data.get("release_ids", [])),
        thread_replies=list(data.get("thread_replies", [])),
        escalation_answers=list(data.get("escalation_answers", [])),
        host_notes=list(data.get("host_notes", [])),
        edge_ops=list(data.get("edge_ops", [])),
        artifact_tasks=list(data.get("artifact_tasks", [])),
        adopt_artifacts=list(data.get("adopt_artifacts", [])),
        proposals=[ProposedSprint(**{k: v for k, v in p.items()
                                     if k in ProposedSprint.__dataclass_fields__})
                   for p in data.get("proposals", [])],
    )
    return StagedCycle(cycle=int(data["cycle"]), output=output,
                       fingerprint=data.get("fingerprint", ""),
                       directive=data.get("directive", ""),
                       host_report_ids=dict(data.get("host_report_ids") or {}),
                       fingerprint_after=data.get("fingerprint_after", ""),
                       host_notes_seen=data.get("host_notes_seen"))


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
        if (not force and fingerprint == pm.failed_fingerprint
                and pm.consecutive_failures >= FAILURE_BACKOFF):
            # Same context, already failed FAILURE_BACKOFF times — retrying spends a
            # full agentic session for the same raise. Wait for something to change.
            # `force` is exempt: the backoff exists to stop the LOOP spinning, and a
            # human pressing Replan/Compress/Brainstorm is precisely the escape hatch
            # from a stuck PM. Without this term the feature disables its own fix.
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            return {"program": program_id, "cycle": cycle, "submitted": [],
                    "proposed": [], "skipped": True, "backoff": True}
        if force:
            # A human asking for this beat is an explicit "try again": clear the count
            # so they get a fresh run of FAILURE_BACKOFF attempts, not one retry that
            # drops straight back behind the gate.
            pm.consecutive_failures = 0
        # About to reason -> capture what changed since the last reasoned cycle.
        new_signals = context_signals(context)
        trigger_labels = _triggers(pm.last_signals, new_signals, force)
        # One housekeeping slot, same pool the wiki draws from: on 09-04 two PM
        # cycles and two wiki runs started within seconds of each other, each
        # having checked a budget that could not see the others.
        holder = f"pm:{program_id}"
        if not housekeeping.acquire(substrate.repo_root, holder, now or time.time()):
            # Same shape as every other skip out of this function. Returning a bare
            # string here made the loop die with "string indices must be integers"
            # on the first refusal in production (2026-09-04).
            return {"program": program_id, "cycle": pm.cycle, "submitted": [],
                    "proposed": [], "skipped": True, "housekeeping_busy": True}

        # Opened before the call, closed after it either way. The reasoner runs
        # in-process, so a loop killed mid-cycle leaves only the start — which is
        # exactly the trace that says a window was spent with nothing to show.
        from coscience.executor import process_token
        token = process_token(os.getpid())      # this loop IS the process doing the call
        # ...which is why a cycle whose end was never written can never retire on its
        # own: the token stays alive as long as the loop does (B3). Starting a new cycle
        # is proof the last one is over, and this loop is the only thing that knows it.
        usage_meter.retire_open_calls(substrate.repo_root, kind="pm",
                                      token=token, program=program_id)
        call_id = usage_meter.start_call(
            substrate.repo_root, "pm", program=program_id, model=context.model,
            limits=usage_meter.current_window(), token=token)

        def _record(ok: bool) -> None:
            housekeeping.release(substrate.repo_root, holder)
            lc = getattr(reasoner, "last_cost", None) or {}
            opened, closed = getattr(reasoner, "last_limits", None) or (None, None)
            usage_meter.finish_call(substrate.repo_root, call_id,
                                    status="ok" if ok else "failed",
                                    cost=lc.get("cost"), tokens=lc.get("tokens"),
                                    turns=lc.get("turns"), usage=lc.get("usage"),
                                    model=context.model,
                                    prompt_bytes=getattr(reasoner, "last_prompt_bytes", None),
                                    limits=closed or usage_meter.current_window(),
                                    limits_before=opened)
        try:
            output = reasoner.run(context)             # the ONE reasoner call
        except Exception:
            # The session ran and spent the window before it raised (a malformed-JSON
            # parse is the common case). A call that leaves no row makes a retry loop
            # invisible in the ledger — see Task 8.
            _record(ok=False)
            # Count it against THIS context: new information resets the counter, so a
            # human approving something always gets a fresh attempt.
            pm.consecutive_failures = (pm.consecutive_failures + 1
                                       if fingerprint == pm.failed_fingerprint else 1)
            pm.failed_fingerprint = fingerprint
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            raise
        _record(ok=True)
        # What this cycle was shown, per server, and the fingerprint the context will
        # have once those reports are folded in — both decided here, where the context
        # the reasoner actually saw is still in hand (a resumed cycle has no context).
        seen_reports: dict[str, list[str]] = {}
        for report in context.host_reports:
            seen_reports.setdefault(str(report.get("host") or ""), []).append(
                str(report.get("id") or ""))
        folded = {str(n.get("host") or "").strip() for n in output.host_notes
                  if isinstance(n, dict)}
        after = ""
        if context.host_reports and folded:
            left = [r for r in context.host_reports if str(r.get("host") or "") not in folded]
            after = context_fingerprint(replace(context, host_reports=left))
        notes_seen = dict(context.host_notes)
        write_staging(substrate, program_id, cycle, output, fingerprint, directive,
                      host_report_ids=seen_reports, fingerprint_after=after,
                      host_notes_seen=notes_seen)  # COMMIT POINT
        staged = StagedCycle(cycle=cycle, output=output, fingerprint=fingerprint,
                             directive=directive, host_report_ids=seen_reports,
                             fingerprint_after=after, host_notes_seen=notes_seen)

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
    slots = program_cap(substrate.load_program(program_id)) - open_proposed
    # A proposal that names no model inherits the program's default worker model.
    worker_model = substrate.load_program(program_id).worker_model
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
            substrate.save_sprint(_proposed_by_pm(Sprint(
                id=sid, status=SprintStatus.PROPOSED, goals=prop.goals,
                plan=list(prop.plan),
                program=program_id, priority=prop.priority,
                resources_required=coerce_resources(prop.resources_required),
                distributed=bool(prop.distributed),
                rationale=prop.rationale,
                title=prop.title,
                summary=prop.summary,
                model=prop.model or worker_model,
            )))
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

    for task in staged.output.artifact_tasks:
        if not isinstance(task, dict):
            continue
        suffix = str(task.get("suffix") or "artifact-update")
        sid = proposal_id(program_id, cycle, suffix)
        if (substrate.sprint_dir(sid) / "sprint.md").is_file():
            if sid not in proposed:
                proposed.append(sid)
            continue
        bound, create = _artifact_specs(task.get("artifact_ids"), task.get("create"))
        if not bound and not create:
            continue                                   # nothing to act on
        if slots <= 0:
            dropped.append(sid)
            continue
        title = str(task.get("title") or "").strip()
        if not title:                                  # PM omitted one — derive
            if create:
                title = "Create: " + ", ".join(c["title"] for c in create)
            elif bound:
                title = "Update " + ", ".join(bound)
            title = title[:80]
        substrate.save_sprint(_proposed_by_pm(Sprint(
            id=sid, status=SprintStatus.PROPOSED, title=title,
            goals=str(task.get("instructions") or "Update the artifact."),
            plan=[], program=program_id, model=worker_model,
            artifacts_bound=bound, artifacts_create=create)))
        slots -= 1
        proposed.append(sid)
        if sid not in pm.proposed_ids:
            submitted.append(sid)

    # --- adoption: output that already exists becomes an artifact right now ---
    # The lightweight counterpart to artifact_tasks above: no sprint, no cap slot,
    # no compute grant. Sources resolve against the program's workdir (the same
    # tree the PM's session explored) and may not escape it. One bad entry — a
    # path outside the tree, an artifact another holder is editing — is skipped,
    # never fatal: the rest of the cycle still applies.
    adopted: list[str] = []
    adopt_skipped: list[dict] = []
    base = _resolve_workdir(substrate, substrate.load_program(program_id).workdir)
    # A sprint writes its output to <substrate>/sprints/<id>/, which is NOT under the
    # program workdir — so "promote the figure that sprint just made" was unreachable
    # by every spelling of the path. Look names up in the substrate root as well, but
    # allow only this program's own sprint dirs, so widening the lookup doesn't let one
    # program adopt another's output.
    own_sprint_dirs = [substrate.sprint_dir(sp.id) for sp in substrate.iter_sprints()
                       if sp.program == program_id]
    for spec in staged.output.adopt_artifacts:
        if not isinstance(spec, dict):
            continue
        aid = str(spec.get("aid") or "").strip()
        if not aid:
            continue
        try:
            sources = artifacts.resolve_sources(
                [base, substrate.repo_root], [str(f) for f in spec.get("files", [])],
                roots=[base, *own_sprint_dirs])
            artifacts.adopt(
                substrate, program_id, aid,
                title=str(spec.get("title") or aid), kind=str(spec.get("kind") or "md"),
                now=now_ts, created_by=f"pm:{program_id}", sources=sources,
                content=str(spec.get("content") or ""),
                filename=str(spec.get("filename") or ""),
                note=str(spec.get("note") or ""))
        except (ValueError, OSError, artifacts.ArtifactBusy) as exc:
            # Never fatal — the rest of the cycle still applies — but never silent
            # either: a swallowed adoption read exactly like the PM never asking.
            adopt_skipped.append({"id": aid, "why": str(exc)})
            continue
        adopted.append(aid)

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
            # Attach artifacts to a still-proposed sprint (e.g. a human asked it to
            # deliver its output as an artifact). Deliverables must be fixed before
            # the sprint runs, so this is PROPOSED-only like goals/plan above.
            if edit.get("artifacts_bound") is not None or edit.get("artifacts_create") is not None:
                bound, create = _artifact_specs(edit.get("artifacts_bound"),
                                                edit.get("artifacts_create"))
                if bound:
                    sp.artifacts_bound = bound
                if create:
                    sp.artifacts_create = create
        if edit.get("priority") is not None:
            try:
                sp.priority = int(edit["priority"])
            except (TypeError, ValueError):
                pass
        # Only a real dict applies ({} clears); a malformed non-dict value from the
        # LLM is ignored (leaves compute unchanged) rather than wiping it.
        if isinstance(edit.get("resources_required"), dict):
            sp.resources_required = coerce_resources(edit["resources_required"])
        if isinstance(edit.get("distributed"), bool):
            sp.distributed = edit["distributed"]
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

        for art in substrate.iter_artifacts(program_id):
            hit = False
            for th in art.threads:
                if th["id"] in replies and threads.needs_reply(th):
                    threads.append(th, "pm", replies[th["id"]], "", now=now_ts)
                    hit = True
            if hit:
                substrate.save_artifact(art)

    # --- escalation answers: the PM's decision for each pm-level escalation shown
    # in its context (resume / reallocate / to_human). Guarded to this program's own
    # sprints, same shape as release/reopen below; the substrate write itself (thread
    # append, status change, progress fields) lives in escalation.answer. ---
    escalations_answered: list[tuple[str, str]] = []
    escalation_skipped: list[dict] = []
    for ans in staged.output.escalation_answers:
        if not isinstance(ans, dict):
            continue
        sid = str(ans.get("sprint_id") or "")
        action = str(ans.get("action") or "")
        if not sid or not action:
            continue
        if not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            escalation_skipped.append({"id": sid, "why": "no such sprint"})
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id:
            escalation_skipped.append({"id": sid, "why": f"belongs to program {sp.program}"})
            continue
        why = escalation.answer(substrate, sid, action,
                                instructions=str(ans.get("instructions") or ""),
                                host=str(ans.get("host") or ""), by="pm", now=now_ts,
                                thread_id=str(ans.get("thread_id") or ""))
        if why:
            escalation_skipped.append({"id": sid, "why": why})
        else:
            escalations_answered.append((sid, action))

    # --- host notes: the program's own knowledge of each server. An entry says the PM
    # has read that server's pending reports (they are dropped); a "text" also rewrites
    # the note. Writing is allowed only for a server this program may actually use, so
    # one program's planner can never leave notes about a machine it has no access to.
    # A bad entry never raises: the cycle's other actions must still apply. ---
    host_notes_updated: list[str] = []
    host_note_skipped: list[dict] = []
    host_notes_folded: list[str] = []     # servers whose reports this cycle cleared
    pool = None
    for entry in staged.output.host_notes:
        if not isinstance(entry, dict):
            continue
        host = entry.get("host")
        if not isinstance(host, str) or not host.strip():
            continue                       # malformed, like a malformed escalation answer
        host = host.strip()
        try:
            check_host_name(host)
        except ValueError:
            host_note_skipped.append({"id": host, "why": "invalid server name"})
            continue
        if pool is None:
            from coscience.resources import load_pool
            pool = load_pool(substrate.repo_root)
        # `local` is a host in the pool like any other, and its top-level `programs:`
        # governs access the same way — so one check covers both.
        h = pool.host(host)
        if h is None or not h.allows(program_id):
            host_note_skipped.append({"id": host, "why": f"this program may not use {host}"})
            continue
        if (staged.host_notes_seen is not None
                and substrate.list_host_notes(program_id).get(host, "")
                != staged.host_notes_seen.get(host, "")):
            # Someone saved this note while the reasoner ran. Theirs stands; the reports
            # they read went with their save, and the planner sees the new note next cycle.
            host_note_skipped.append({"id": host, "why": "a human edited it while this cycle ran"})
            continue
        try:
            if isinstance(entry.get("text"), str):
                # Only a real string rewrites the note. Anything else (a null, a number)
                # means the entry carries no text at all — see the parser.
                substrate.save_host_note(program_id, host, entry["text"])
                host_notes_updated.append(host)
            # Only the reports this cycle was actually shown: one filed while the reasoner
            # ran has not been read by anyone yet, and clearing it would lose it unread.
            substrate.clear_host_reports(program_id, host,
                                         ids=staged.host_report_ids.get(host, []))
            host_notes_folded.append(host)
        except OSError as exc:
            # A note that cannot be written (a full disk, a name the filesystem refuses)
            # must not abort the apply: the cycle's releases, replies and report are
            # worth more than one note, and an unhandled raise here would re-apply this
            # same staged cycle every beat and wedge the program's planner for good.
            host_note_skipped.append({"id": host, "why": f"could not be written: {exc}"})

    # --- release: put an APPROVED sprint into production (-> queued). The approved
    # pool is the PM's managed queue; it releases items here as it sees need, and the
    # dispatcher runs queued sprints by priority as compute frees. Guarded to this
    # program's approved sprints. ---
    released: list[str] = []
    release_skipped: list[dict] = []
    for sid in staged.output.release_ids:
        sid = str(sid)
        if not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            # Most often a suffix or a mistyped id. Silently dropping it made a lost
            # release indistinguishable from a deliberate hold — say which it was.
            release_skipped.append({"id": sid, "why": "no such sprint"})
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id:
            release_skipped.append({"id": sid, "why": f"belongs to program {sp.program}"})
            continue
        if sp.status != SprintStatus.APPROVED:
            release_skipped.append({"id": sid, "why": f"status is {sp.status.value}, not approved"})
            continue
        set_status(sp, SprintStatus.QUEUED, by="pm", action="run")
        sp.hold = {}          # releasing it IS the answer to whatever it was held for
        substrate.save_sprint(sp)
        released.append(sid)

    # --- hold: say why an APPROVED sprint is deliberately not being released yet.
    # The status does not move. This replaced `reopen`, which sent the sprint back to
    # PROPOSED: the PM can un-approve but cannot approve, so reopening destroyed a
    # human authorization it had no power to restore, and every reopen ever recorded
    # did exactly that — to sequence work, which holding does without the damage.
    # Guarded to this program's approved sprints; queued/executing work is already
    # released and is not the PM's to hold.
    held: list[str] = []
    hold_skipped: list[dict] = []
    for entry in staged.output.holds:
        if not isinstance(entry, dict):
            hold_skipped.append({"id": str(entry), "why": "not an object with id and why"})
            continue
        sid = str(entry.get("id") or "")
        why = str(entry.get("why") or "").strip()
        if not (substrate.sprint_dir(sid) / "sprint.md").is_file():
            hold_skipped.append({"id": sid, "why": "no such sprint"})
            continue
        if not why:
            # A hold with no reason is the invisible non-action it replaced.
            hold_skipped.append({"id": sid, "why": "no reason given for the hold"})
            continue
        sp = substrate.load_sprint(sid)
        if sp.program != program_id:
            hold_skipped.append({"id": sid, "why": f"belongs to program {sp.program}"})
            continue
        if sp.status != SprintStatus.APPROVED:
            hold_skipped.append({"id": sid, "why": f"status is {sp.status.value}, not approved"})
            continue
        if sid in released:
            hold_skipped.append({"id": sid, "why": "released this cycle"})
            continue
        sp.hold = {"why": hold_reason(why), "at": time.time(), "by": "pm"}
        substrate.save_sprint(sp)
        held.append(sid)

    actions = {"released": released, "held": held, "submitted": submitted,
               "dropped": dropped, "adopted": adopted,
               "ideas_added": ideas_added, "ideas_removed": ideas_removed,
               "release_skipped": release_skipped, "hold_skipped": hold_skipped,
               "adopt_skipped": adopt_skipped,
               "escalations_answered": escalations_answered,
               "escalation_skipped": escalation_skipped,
               "host_notes_updated": host_notes_updated,
               "host_note_skipped": host_note_skipped}
    actions["unbacked_claims"] = unbacked_claims(staged.output.report, actions)
    # The reasoner's prose, then the platform's own record of what it applied.
    substrate.save_report(program_id, staged.output.report + actions_ledger(actions),
                          cycle=cycle)
    record_sprint_notes(substrate, staged.output.report, actions, cycle, now_ts)

    pm.cycle = cycle + 1
    pm.last_run = now_ts
    # A report wakes the planner ONCE. The stored fingerprint is the pre-apply one, so
    # folding reports in would otherwise change the context by the planner's own hand
    # and buy a second cycle with nothing new in it. When the fold-in went as staged,
    # store the fingerprint that context will now have instead.
    folded_as_staged = (staged.fingerprint_after
                        and set(host_notes_folded) == {h for h in staged.host_report_ids
                                                       if staged.host_report_ids[h]}
                        & {str(n.get("host") or "").strip()
                           for n in staged.output.host_notes if isinstance(n, dict)})
    pm.last_fingerprint = staged.fingerprint_after if folded_as_staged else staged.fingerprint
    for sid in proposed:
        if sid not in pm.proposed_ids:
            pm.proposed_ids.append(sid)
    pm.log.append(f"cycle {cycle}: proposed {proposed}"
                  + (f", released {released}" if released else "")
                  + (f", held {held}" if held else "")
                  + (f", dropped {dropped} (cap)" if dropped else "")
                  + (f", FAILED to release {[s['id'] for s in release_skipped]}"
                     if release_skipped else "")
                  + (f", FAILED to adopt {[s['id'] for s in adopt_skipped]}"
                     if adopt_skipped else "")
                  + (f", UNBACKED CLAIMS {actions['unbacked_claims']}"
                     if actions["unbacked_claims"] else ""))
    if new_signals is not None:                        # we actually reasoned this beat
        pm.last_signals = new_signals
        pm.consecutive_failures = 0
        pm.failed_fingerprint = ""
        # Releases live here too, not just in the loop's log file: pm.md travels with
        # the substrate, so a dropped action stays evidence after the log rotates.
        pm.activations.append({
            "at": now_ts, "cycle": cycle, "triggers": trigger_labels,
            "submitted": list(submitted), "forced": bool(force),
            "released": list(released), "held": list(held),
            "release_skipped": [dict(s) for s in release_skipped],
            "hold_skipped": [dict(s) for s in hold_skipped],
            "adopt_skipped": [dict(s) for s in adopt_skipped],
            "escalations_answered": list(escalations_answered),
            "escalation_skipped": [dict(s) for s in escalation_skipped],
            "unbacked_claims": list(actions["unbacked_claims"]),
        })
        pm.activations = pm.activations[-50:]          # keep the recent timeline bounded
    substrate.save_pm_state(pm)

    clear_staging(substrate, program_id)
    return {"program": program_id, "cycle": cycle, "submitted": submitted,
            "proposed": proposed, "dropped": dropped, "skipped": False,
            "ideas_added": ideas_added, "ideas_removed": ideas_removed,
            "pool_size": len(ideas_by_id), "adopted": adopted,
            "edges_added": edges_added, "edges_removed": edges_removed,
            "released": released, "held": held,
            "release_skipped": release_skipped, "hold_skipped": hold_skipped,
            "adopt_skipped": adopt_skipped,
            "escalations_answered": escalations_answered,
            "escalation_skipped": escalation_skipped,
            "host_notes_updated": host_notes_updated,
            "host_note_skipped": host_note_skipped,
            "unbacked_claims": actions["unbacked_claims"]}
