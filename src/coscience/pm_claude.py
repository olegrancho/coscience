"""Real PM reasoner: a headless Claude Code session behind the Reasoner seam.

render_prompt and parse_response are pure and contract-tested; the single
side-effecting step (invoke) shells out to the `claude` binary and is injectable
so the unit suite never calls a live LLM. A bad response raises PMReasonerError,
which propagates out of run() before pm_beat's staging commit (nothing staged)."""
from __future__ import annotations

import json
import re
import subprocess

from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint


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
    return f"""You are the PM agent for a research program. Propose the next sprint(s)
and write a short status report. You only PROPOSE; humans approve.

PROGRAM GOALS:
{context.goals}

OPEN SPRINTS (already proposed/approved/running — do not duplicate these):
{open_block}

COMPLETED SPRINTS AND RESULTS (use these to decide what is most valuable next):
{done_block}

PRIOR PROPOSALS you already made (do NOT repeat their intent): {prior_block}

Respond with ONLY a JSON object (no prose outside it) of this shape:
{{"report": "<markdown program-status summary>",
  "proposals": [
    {{"suffix": "<short-slug>", "goals": "<what this sprint does>",
      "plan": [{{"id": "<step-id>", "run": "<shell command>"}}],
      "priority": <int>, "resources_required": {{}} or null,
      "rationale": "<why this experiment next>"}}
  ]}}
Propose 0 proposals if nothing new is warranted. Keep `plan` steps concrete and runnable.
"""


def _extract_json(text: str) -> str:
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise PMReasonerError("no JSON object found in reasoner output")
    return text[start:end + 1]


def parse_response(text: str) -> PMCycleOutput:
    try:
        data = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise PMReasonerError(f"invalid reasoner JSON: {exc}") from exc
    proposals = []
    for p in data.get("proposals", []):
        try:
            proposals.append(ProposedSprint(
                suffix=str(p["suffix"]), goals=str(p["goals"]),
                plan=[{"id": str(s["id"]), "run": str(s["run"])} for s in p["plan"]],
                priority=int(p.get("priority", 0)),
                resources_required=p.get("resources_required"),
                rationale=str(p.get("rationale", "")),
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
