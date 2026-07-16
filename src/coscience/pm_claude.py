"""Real PM reasoner: a headless Claude Code session behind the Reasoner seam.

render_prompt and parse_response are pure and contract-tested; the single
side-effecting step (invoke) shells out to the `claude` binary and is injectable
so the unit suite never calls a live LLM. A bad response raises PMReasonerError,
which propagates out of run() before pm_beat's staging commit (nothing staged)."""
from __future__ import annotations

import json
import re
import subprocess

from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint, coerce_resources


class PMReasonerError(Exception):
    """The reasoner produced no usable PMCycleOutput."""


def render_prompt(context: PMContext) -> str:
    def _lines(items, fmt):
        return "\n".join(fmt(i) for i in items) or "(none)"

    open_block = _lines(context.open_sprints,
                        lambda s: f"- {s['id']} [{s['status']}, priority {s.get('priority', 0)}]: {s['goals']}")
    done_block = _lines(context.completed,
                        lambda s: f"- {s['id']}: {s['goals']} -> result: {s['result']}")
    failed_block = _lines(context.failed,
                          lambda s: f"- {s['id']}: {s['goals']} -> FAILED: {s['error']}")
    def _feedback_line(f):
        history = " | ".join(f"{m['role']}: {m['text']}" for m in f["messages"])
        return (f"- {f['sprint_id']} [{f['status']}, "
                f"{'EDITABLE' if f['editable'] else 'locked — propose a follow-up instead'}, "
                f"thread {f['thread_id']}]: {history}")
    feedback_block = _lines(context.sprint_feedback, _feedback_line)

    def _idea_feedback_line(f):
        history = " | ".join(f"{m['role']}: {m['text']}" for m in f["messages"])
        return f"- idea [{f['idea_id']}], thread {f['thread_id']}: {history}"
    idea_feedback_block = _lines(context.idea_feedback, _idea_feedback_line)

    def _guidance_feedback_line(f):
        history = " | ".join(f"{m['role']}: {m['text']}" for m in f["messages"])
        return f"- thread {f['thread_id']}: {history}"
    guidance_feedback_block = _lines(context.guidance_feedback, _guidance_feedback_line)
    prior_block = ", ".join(context.prior_proposals) or "(none)"
    guidance_block = ""
    if context.human_guidance:
        notes = "\n".join(f"- {g}" for g in context.human_guidance)
        guidance_block = (
            "\n\nHUMAN GUIDANCE (standing direction from the oversight committee "
            "— weigh these in your proposals):\n" + notes)

    def _idea_line(i):
        flags = []
        if i.get("source") == "human":
            flags.append("human")
        if i.get("pinned"):
            flags.append("PINNED — protected, do NOT delete")
        if i.get("demoted"):
            flags.append("DEMOTED — do NOT promote to a sprint")
        tag = f" ({'; '.join(flags)})" if flags else ""
        return f"- [{i['id']}] {i['text']}{tag}"
    ideas_block = _lines(context.ideas, _idea_line)

    directive_block = ""
    if context.directive == "compress":
        directive_block = (
            "\n\n*** DIRECTIVE THIS CYCLE: COMPRESS THE IDEA POOL ***\n"
            "The human asked you to CONTRACT and tidy the pool now. Aggressively consolidate:\n"
            "- You MAY merge and delete ANY idea that is NOT marked PINNED. PINNED ideas stay intact "
            "(never delete or reword them) — everything else is fair game.\n"
            "- MERGE overlapping ideas: write ONE combined idea in new_ideas and list the originals' ids "
            "in delete_idea_ids. Prune settled, disproven, duplicate, or low-value ideas.\n"
            "- RE-RANK the survivors: set idea_order to ALL surviving idea ids, most-promising first "
            "(include pinned ids and the ids of any merged ideas you add).\n"
            "- Do NOT propose sprints this cycle; focus on the pool. Refresh ideas_summary.\n"
        )
    elif context.directive == "brainstorm":
        directive_block = (
            "\n\n*** DIRECTIVE THIS CYCLE: BRAINSTORM — EXPAND THE IDEA POOL ***\n"
            "The human asked you to GROW the pool now. Generate roughly 4-8 GENUINELY NEW, diverse "
            "candidate directions (new_ideas, ~1 paragraph each) — not already in the pool and not "
            "rewordings of existing ideas. Do NOT prune existing ideas and do NOT propose sprints this "
            "cycle; just add fresh ideas and refresh ideas_summary.\n"
        )

    return f"""You are the PM agent for a research program. You maintain two things:
a small set of PROPOSED SPRINTS (concrete next experiments, which humans approve), and
an IDEA POOL (short, vague candidate directions you grow and prune over time). You only
PROPOSE and curate; humans approve sprints.{directive_block}

Your session runs in this program's working directory. If the goals refer to "this
folder", "the data here", or "existing work", they mean your current working
directory — inspect it there; do NOT go hunting up the filesystem tree.

PROGRAM GOALS:
{context.goals}{guidance_block}

OPEN SPRINTS (already proposed/approved/queued/running — do not duplicate these).
APPROVED sprints are a human-authorized queue that YOU manage: decide when each should
run and in what order. Release one into production with release_ids when it's the right
next thing (dependencies satisfied, worth the compute); retune ordering with priority in
sprint_edits. You need not release them all at once — sequence them as results land. And if
an approved sprint no longer makes sense to run or needs serious rework, send it back to
'proposed' (reopen_ids) rather than releasing it.
{open_block}

COMPLETED SPRINTS AND RESULTS (use these to decide what is most valuable next):
{done_block}

FAILED SPRINTS (the agent gave up after repeated errors — read the reason and react:
propose a corrected/rescoped sprint, change the approach, or record an idea; do NOT
blindly re-propose the same thing):
{failed_block}

HUMAN FEEDBACK ADDRESSED TO YOU about specific sprints — each shown as an open thread id
and its message history (act on each: if it is EDITABLE, revise that sprint via
sprint_edits; if it is locked, propose a follow-up or adjust your plan instead).
FEEDBACK THREADS: for each open thread shown, take the action it asks for (edit the
sprint, change compute, propose, curate) AND add a short thread_replies entry saying
what you did. If you can't, say why.
{feedback_block}

HUMAN FEEDBACK ADDRESSED TO YOU about specific pool ideas below — same thread_replies
mechanism as sprint feedback: react to it (develop the idea, promote it, revise the idea
pool, or curate accordingly) AND add a thread_replies entry with that idea's thread id
saying what you did, or why not.
IDEA FEEDBACK:
{idea_feedback_block}

GUIDANCE FEEDBACK ADDRESSED TO YOU — new standing-guidance messages open below need your
action: same thread_replies mechanism as sprint/idea feedback — weigh each into your
proposals/idea curation (adjust plans, curate ideas, whatever it calls for) AND add a
thread_replies entry with that guidance thread's id saying what you did, or why not.
GUIDANCE FEEDBACK:
{guidance_feedback_block}

PRIOR PROPOSALS you already made (do NOT repeat their intent): {prior_block}

IDEA POOL (id in brackets; you may delete ANY idea that is not PINNED — pinned == protected):
{ideas_block}

SPRINT CAP: at most {context.max_proposed} sprints may await review. {context.proposed_count} are
pending now, so you have {context.free_slots} free slot(s). Propose/promote AT MOST {context.free_slots};
if that is 0, propose nothing and instead curate the idea pool.

Respond with ONLY a JSON object (no prose outside it) of this shape:
{{"report": "<program-status report as STRUCTURED markdown a reader understands at a glance, WITHOUT needing prior context. Always cover, in THIS order, each under a bold heading: **Findings** — the most important and most recent results so far and what they mean (if none yet, say so plainly); **Rationale** — why the currently proposed experiments are the right next moves; **Status & next steps** — where the program stands and what happens next. Do NOT reduce the report to just next steps (e.g. 'waiting for results') — the findings and rationale must always be there. NOT one run-on paragraph: a bold one-line headline, a blank line, then the headed sections with short paragraphs and/or '-' bullets, a blank line between blocks. Put real newlines in the JSON string (escaped as \\n).>",
  "ideas_summary": "<short markdown summary of the whole idea pool: themes, what's promising, what you pruned and why>",
  "new_ideas": ["<a one-paragraph candidate direction>", "..."],
  "delete_idea_ids": ["<id of any NON-PINNED idea to prune>", "..."],
  "idea_order": ["<idea id — used when COMPRESSing: ALL surviving ids, most-promising first; omit/empty otherwise>", "..."],
  "sprint_edits": [
    {{"sprint_id": "<an EDITABLE (still-proposed) sprint to revise per feedback>",
      "goals": "<rewritten objective, optional>", "plan": ["<revised step>", "..."],
      "summary": "<optional>", "title": "<optional>", "priority": <int, optional>,
      "resources_required": {{}} or null}}
  ],
  "reopen_ids": ["<id of an APPROVED sprint (see OPEN SPRINTS) to send back to 'proposed' for
                 reconsideration: results made it obsolete/redundant, it no longer makes sense
                 to run at all, or it needs serious rework before it's worth running. Only
                 approved sprints — never queued/running ones. Omit/empty if none.>"],
  "release_ids": ["<id of an APPROVED sprint to release into production now — it becomes
                 'queued' and the scheduler runs it as compute frees. Release the ones whose
                 time has come; hold the rest. Only approved sprints. Omit/empty if none.>"],
  "thread_replies": [{{"thread_id": "<id of an open feedback thread shown above,
                       whether on a sprint, a pool idea, or standing guidance>",
                       "text": "<short reply: what you did in response, or why you can't>"}}],
  "proposals": [
    {{"suffix": "<short-slug>",
      "title": "<=8 words naming the experiment, e.g. 'Cross-validate the witness pair'>",
      "summary": "one or two plain sentences a reviewer can skim to decide",
      "goals": "<the full objective of this sprint>",
      "plan": ["<suggested step in plain language>", "<another>", "..."],
      "priority": <int>, "resources_required": {{}} or null,
      "rationale": "<why this experiment next>",
      "from_idea": "<id of the pool idea this promotes, or omit>",
      "model": "<optional: a Claude model slug to run this sprint's worker on, e.g. 'claude-sonnet-4-6' for cheap/routine work or 'claude-opus-4-8' for hard reasoning; omit to use the default>"}}
  ]}}
Propose 0 proposals if nothing new is warranted, or you are at the cap.

Run the program by curating ideas, not by piling on sprints:
- Keep the idea pool small and alive. As results arrive, PRUNE ideas that are settled,
  disproven, or obsolete (delete_idea_ids — any NON-PINNED idea). ADD new ideas
  (new_ideas, ~1 paragraph each) when results suggest fresh directions.
- PROMOTE an idea to a sprint only when it is genuinely promising AND you have a free
  slot: emit a proposal with `from_idea` set to that idea's id (it leaves the pool).
- Ideas marked PINNED are protected — never delete them. (Human-made, commented-on, and
  demoted ideas are auto-pinned, so they start protected; treat human comments as
  direction.) Everything not pinned is fair game to prune.
- MANAGE THE APPROVED QUEUE: these are authorized and waiting on you. Each cycle, release
  (release_ids) the approved sprint(s) that should run next and hold the rest until their
  prerequisites/prior results are in; use priority to order what's pending. Don't leave
  authorized work sitting idle with no reason — if it's ready and useful, release it.

Each sprint is carried out by a capable autonomous research agent that plans and does
the work itself. So:
- Size a sprint as a substantial unit of work — roughly a few days to a week — not a
  single command. Propose meaningful experiments, not one-liners.
- `plan` is a SHORT list (<=5) of SUGGESTED steps in plain language — high-level
  guidance for the agent, NOT shell commands or code. Describe WHAT to do and what a
  good result looks like; let the agent figure out how. Never put `python3 -c`,
  `printf`, file redirects, or any executable command in `plan`.
`resources_required` maps a resource name to a NUMBER only (e.g. {{"cpu": 1}} or {{"gpu": 2}}),
or {{}} — never put notes or prose in it; put caveats in `rationale`.
You may also change an editable sprint's resources_required (compute) here in response to
feedback — e.g. drop a gpu the environment can't provide and run on cpu.
`title` is a short headline; `summary` is the skimmable gist; `goals` is the full objective.
"""


def _decode_json_object(text: str) -> dict:
    # Skip an optional ```json fence opener, then raw_decode one object from the
    # first '{' so trailing prose / closing fences / nested braces don't break it.
    m = re.search(r"```(?:json)?\s*", text)
    region = text[m.end():] if m else text
    start = region.find("{")
    if start == -1:
        raise PMReasonerError("no JSON object found in reasoner output")
    try:
        obj, _ = json.JSONDecoder().raw_decode(region[start:])
    except json.JSONDecodeError as exc:
        raise PMReasonerError(f"invalid reasoner JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise PMReasonerError("reasoner JSON is not an object")
    return obj


def parse_response(text: str) -> PMCycleOutput:
    data = _decode_json_object(text)
    proposals = []
    for p in data.get("proposals", []):
        try:
            proposals.append(ProposedSprint(
                suffix=str(p["suffix"]), goals=str(p["goals"]),
                plan=[str(s) for s in p.get("plan", [])],
                priority=int(p.get("priority", 0)),
                resources_required=coerce_resources(p.get("resources_required")),
                rationale=str(p.get("rationale", "")),
                title=str(p.get("title", "")),
                summary=str(p.get("summary", "")),
                from_idea=str(p.get("from_idea", "")),
                model=str(p.get("model", "")),
            ))
        except (KeyError, TypeError) as exc:
            raise PMReasonerError(f"malformed proposal: {exc}") from exc
    edits = [e for e in data.get("sprint_edits", []) if isinstance(e, dict) and e.get("sprint_id")]
    return PMCycleOutput(
        proposals=proposals,
        report=str(data.get("report", "")),
        ideas_summary=str(data.get("ideas_summary", "")),
        new_ideas=[str(s) for s in data.get("new_ideas", [])],
        delete_idea_ids=[str(s) for s in data.get("delete_idea_ids", [])],
        idea_order=[str(s) for s in data.get("idea_order", [])],
        sprint_edits=edits,
        reopen_ids=[str(s) for s in data.get("reopen_ids", [])],
        release_ids=[str(s) for s in data.get("release_ids", [])],
        thread_replies=[dict(r) for r in data.get("thread_replies", [])
                        if isinstance(r, dict) and r.get("thread_id")],
    )


class ClaudeCodeReasoner:
    """Reasoner backed by a headless Claude Code session. `invoke` is injectable
    for testing; the default shells out to the `claude` binary."""

    def __init__(self, invoke=None, claude_bin: str = "claude"):
        self.claude_bin = claude_bin
        self._invoke = invoke or self._default_invoke
        self.last_cost: dict | None = None     # {cost, tokens} of the most recent call

    def _default_invoke(self, prompt: str, model: str = "", cwd: str = "") -> str:
        # --output-format json gives us the reply text plus cost/token usage in one
        # envelope; we unwrap `result` and stash the cost for the dashboard.
        cmd = [self.claude_bin, "-p", prompt, "--output-format", "json"]
        if model:
            cmd += ["--model", model]
        # Run in the program's workdir so the tool-enabled session explores that
        # tree, not whatever cwd the loop process happened to launch from.
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or None)
        if proc.returncode != 0:
            raise PMReasonerError(
                f"claude exited {proc.returncode}: {(proc.stderr or '')[:200]}")
        try:
            env = json.loads(proc.stdout)
            usage = env.get("usage") or {}
            self.last_cost = {"cost": env.get("total_cost_usd"),
                              "tokens": sum(int(usage.get(k, 0) or 0) for k in (
                                  "input_tokens", "output_tokens",
                                  "cache_creation_input_tokens", "cache_read_input_tokens"))}
            return str(env.get("result") or "")
        except (json.JSONDecodeError, AttributeError):
            return proc.stdout or ""

    def run(self, context: PMContext) -> PMCycleOutput:
        prompt = render_prompt(context)
        # Injected invokes (tests) may take fewer args; degrade prompt+model+cwd ->
        # prompt+model -> prompt so the seam stays easy to fake.
        try:
            out = self._invoke(prompt, context.model, context.workdir)
        except TypeError:
            try:
                out = self._invoke(prompt, context.model)
            except TypeError:
                out = self._invoke(prompt)
        return parse_response(out)


def render_chat_prompt(context: PMContext, history: list[dict], message: str) -> str:
    """A conversational prompt: the PM answers a human's question about the program
    with full context. Answer-only — it does not act (the human acts via the UI)."""
    def _lines(items, fmt):
        return "\n".join(fmt(i) for i in items) or "(none)"
    open_block = _lines(context.open_sprints, lambda s: f"- {s['id']} [{s['status']}]: {s['goals']}")
    done_block = _lines(context.completed, lambda s: f"- {s['id']}: {s['goals']} -> {s['result']}")
    ideas_block = _lines(context.ideas, lambda i: f"- {i['text']}")
    guidance_block = _lines(context.human_guidance, lambda g: f"- {g}")
    convo = "\n".join(f"{'PM' if m['role'] == 'pm' else 'Human'}: {m['text']}" for m in history) \
        or "(start of conversation)"
    return f"""You are the PM (planning) agent for a research program, in a direct chat with the
human overseer. Answer their questions about the program clearly and concisely. You may
explain your reasoning, discuss trade-offs, and suggest what could be done next — but you
do NOT take actions here; the human acts via the dashboard (approve/propose/comment/guide).
Reply in plain prose or markdown. Do NOT output JSON.

Your session runs in this program's working directory; "this folder"/"the data here"
means your current working directory — inspect it there, don't search the wider tree.

PROGRAM GOALS:
{context.goals}

OPEN SPRINTS (proposed / approved / running):
{open_block}

COMPLETED SPRINTS AND RESULTS:
{done_block}

IDEA POOL:
{ideas_block}

STANDING GUIDANCE:
{guidance_block}

CONVERSATION SO FAR:
{convo}

Human: {message}
PM:"""


def chat_reply(context: PMContext, history: list[dict], message: str,
               claude_bin: str = "claude") -> str:
    """Shell the `claude` binary for one conversational PM reply (plain text)."""
    prompt = render_chat_prompt(context, history, message)
    cmd = [claude_bin, "-p", prompt, "--output-format", "text"]
    if context.model:
        cmd += ["--model", context.model]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=context.workdir or None)
    if proc.returncode != 0:
        raise PMReasonerError(f"claude exited {proc.returncode}: {(proc.stderr or '')[:200]}")
    return (proc.stdout or "").strip()
