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
    prior_block = ", ".join(context.prior_proposals) or "(none)"
    guidance_block = ""
    if context.human_guidance:
        notes = "\n".join(f"- {g}" for g in context.human_guidance)
        guidance_block = (
            "\n\nHUMAN GUIDANCE (standing direction from the oversight committee "
            "— weigh these in your proposals):\n" + notes)
    return f"""You are the PM agent for a research program. Propose the next sprint(s)
and write a short status report. You only PROPOSE; humans approve.

PROGRAM GOALS:
{context.goals}{guidance_block}

OPEN SPRINTS (already proposed/approved/running — do not duplicate these):
{open_block}

COMPLETED SPRINTS AND RESULTS (use these to decide what is most valuable next):
{done_block}

PRIOR PROPOSALS you already made (do NOT repeat their intent): {prior_block}

Respond with ONLY a JSON object (no prose outside it) of this shape:
{{"report": "<markdown program-status summary>",
  "proposals": [
    {{"suffix": "<short-slug>",
      "title": "<=8 words naming the experiment, e.g. 'Cross-validate the witness pair'>",
      "summary": "one or two plain sentences a reviewer can skim to decide",
      "goals": "<the full objective of this sprint>",
      "plan": ["<suggested step in plain language>", "<another>", "..."],
      "priority": <int>, "resources_required": {{}} or null,
      "rationale": "<why this experiment next>"}}
  ]}}
Propose 0 proposals if nothing new is warranted.

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
            ))
        except (KeyError, TypeError) as exc:
            raise PMReasonerError(f"malformed proposal: {exc}") from exc
    return PMCycleOutput(proposals=proposals, report=str(data.get("report", "")))


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
