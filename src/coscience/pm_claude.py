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
                        lambda s: f"- {s['id']} [{s['status']}]: {s['goals']}")
    done_block = _lines(context.completed,
                        lambda s: f"- {s['id']}: {s['goals']} -> result: {s['result']}")
    failed_block = _lines(context.failed,
                          lambda s: f"- {s['id']}: {s['goals']} -> FAILED: {s['error']}")
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
        if i.get("protected"):
            flags.append("PROTECTED")
        if i.get("comments"):
            flags.append("comments: " + " | ".join(i["comments"]))
        tag = f" ({'; '.join(flags)})" if flags else ""
        return f"- [{i['id']}] {i['text']}{tag}"
    ideas_block = _lines(context.ideas, _idea_line)

    return f"""You are the PM agent for a research program. You maintain two things:
a small set of PROPOSED SPRINTS (concrete next experiments, which humans approve), and
an IDEA POOL (short, vague candidate directions you grow and prune over time). You only
PROPOSE and curate; humans approve sprints.

PROGRAM GOALS:
{context.goals}{guidance_block}

OPEN SPRINTS (already proposed/approved/running — do not duplicate these):
{open_block}

COMPLETED SPRINTS AND RESULTS (use these to decide what is most valuable next):
{done_block}

FAILED SPRINTS (the agent gave up after repeated errors — read the reason and react:
propose a corrected/rescoped sprint, change the approach, or record an idea; do NOT
blindly re-propose the same thing):
{failed_block}

PRIOR PROPOSALS you already made (do NOT repeat their intent): {prior_block}

IDEA POOL (id in brackets; you may delete only your own non-PROTECTED ideas):
{ideas_block}

SPRINT CAP: at most {context.max_proposed} sprints may await review. {context.proposed_count} are
pending now, so you have {context.free_slots} free slot(s). Propose/promote AT MOST {context.free_slots};
if that is 0, propose nothing and instead curate the idea pool.

Respond with ONLY a JSON object (no prose outside it) of this shape:
{{"report": "<markdown program-status summary>",
  "ideas_summary": "<short markdown summary of the whole idea pool: themes, what's promising, what you pruned and why>",
  "new_ideas": ["<a one-paragraph candidate direction>", "..."],
  "delete_idea_ids": ["<id of one of YOUR non-protected ideas to prune>", "..."],
  "proposals": [
    {{"suffix": "<short-slug>",
      "title": "<=8 words naming the experiment, e.g. 'Cross-validate the witness pair'>",
      "summary": "one or two plain sentences a reviewer can skim to decide",
      "goals": "<the full objective of this sprint>",
      "plan": ["<suggested step in plain language>", "<another>", "..."],
      "priority": <int>, "resources_required": {{}} or null,
      "rationale": "<why this experiment next>",
      "from_idea": "<id of the pool idea this promotes, or omit>"}}
  ]}}
Propose 0 proposals if nothing new is warranted, or you are at the cap.

Run the program by curating ideas, not by piling on sprints:
- Keep the idea pool small and alive. As results arrive, PRUNE ideas that are settled,
  disproven, or obsolete (delete_idea_ids — only your own, non-protected). ADD new ideas
  (new_ideas, ~1 paragraph each) when results suggest fresh directions.
- PROMOTE an idea to a sprint only when it is genuinely promising AND you have a free
  slot: emit a proposal with `from_idea` set to that idea's id (it leaves the pool).
- Ideas marked PROTECTED (human-proposed, pinned, or commented-on) are off-limits to
  deletion — treat human comments on them as direction.

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
            ))
        except (KeyError, TypeError) as exc:
            raise PMReasonerError(f"malformed proposal: {exc}") from exc
    return PMCycleOutput(
        proposals=proposals,
        report=str(data.get("report", "")),
        ideas_summary=str(data.get("ideas_summary", "")),
        new_ideas=[str(s) for s in data.get("new_ideas", [])],
        delete_idea_ids=[str(s) for s in data.get("delete_idea_ids", [])],
    )


class ClaudeCodeReasoner:
    """Reasoner backed by a headless Claude Code session. `invoke` is injectable
    for testing; the default shells out to the `claude` binary."""

    def __init__(self, invoke=None, claude_bin: str = "claude"):
        self.claude_bin = claude_bin
        self._invoke = invoke or self._default_invoke

    def _default_invoke(self, prompt: str) -> str:
        proc = subprocess.run(
            [self.claude_bin, "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise PMReasonerError(
                f"claude exited {proc.returncode}: {(proc.stderr or '')[:200]}")
        return proc.stdout or ""

    def run(self, context: PMContext) -> PMCycleOutput:
        return parse_response(self._invoke(render_prompt(context)))
