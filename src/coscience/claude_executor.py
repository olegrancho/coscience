"""The production runner: one long-lived Claude agent carries out a whole sprint.

The worker writes an `instructions.md` into the sprint folder and launches
`claude -p "read instructions ... and execute" --dangerously-skip-permissions`
detached. The agent plans and does the work itself, keeps a scratchpad with
checkpoints (so it can resume if interrupted), and watches its own Claude usage.
We capture its output + exit code so the worker can collect a result when it ends."""
from __future__ import annotations

import shlex
from pathlib import Path

from coscience.executor import (ExecutionContext, is_running, launch_detached,
                                terminate_detached)
from coscience.models import Sprint

USAGE_CMD = "python3 ~/.claude/skills/usage/usage.py"


def build_instructions(sprint: Sprint, context: "ExecutionContext | None",
                       scratchpad: Path) -> str:
    steps = "\n".join(f"- {s}" for s in sprint.plan) or "(none given — plan the work yourself)"
    program = ""
    prior = "None yet."
    comments = ""
    if context is not None:
        program = f"{context.program_title}: {context.program_goal}".strip(": ").strip()
        if context.prior_results:
            prior = "\n\n".join(context.prior_results)
        if context.human_comments:
            notes = "\n".join(f"- {c}" for c in context.human_comments)
            comments = ("\n\n## Human feedback on this sprint (weigh this — it is direction "
                        "from your reviewers)\n" + notes)
    return f"""# Sprint: {sprint.title or sprint.id}

You are an autonomous research agent. Carry out this sprint end to end, unattended.
Do the work yourself — run commands, write and read files. Do NOT ask for permission
or confirmation, and do NOT merely describe what you would do: actually do it.

## Program goal
{program or "(see the sprint objective below)"}

## This sprint
{sprint.summary}

Objective:
{sprint.goals}
{comments}

## Suggested steps (guidance only — you decide the actual work)
{steps}

## Prior results in this program (read before redoing anything)
{prior}

## How to work (autonomous mode)
1. Keep a scratchpad at {scratchpad}. Record what you are doing, key decisions, and
   checkpoints as you go. If that file already exists, you were interrupted before —
   read it first and continue from the last checkpoint instead of starting over.
2. Watch your Claude usage — running out is the most likely thing to kill you mid-run.
   Run `{USAGE_CMD}` regularly. As you approach the limit of the 5-hour window (~85%),
   write a checkpoint to the scratchpad and wind down to a safe stopping point rather
   than starting major new work; you will resume on the next run.
3. Use your best judgment. Derive decisions from the goal, the guidance, and sane
   defaults, and keep moving. Only stop for something truly irreversible or a real
   blocker; record such calls in the scratchpad.
4. When the sprint is done, print your findings as your final message — the answer,
   how you reached it, the key evidence/witnesses, and any caveats. That final message
   is recorded as the sprint result.
"""


class ClaudeAgent:
    """Launches and supervises one detached Claude agent per sprint. The worker
    calls start -> is_running (poll) -> collect, and stop to preempt."""

    def __init__(self, claude_bin: str = "claude"):
        self.claude_bin = claude_bin

    @staticmethod
    def _paths(sprint_dir: Path):
        return (sprint_dir / "instructions.md", sprint_dir / "scratchpad.md",
                sprint_dir / "agent.out", sprint_dir / "agent.exit")

    def start(self, sprint: Sprint, context: "ExecutionContext | None",
              sprint_dir: Path, repo_root: "Path | None" = None) -> str:
        sprint_dir.mkdir(parents=True, exist_ok=True)
        instr, scratch, out, exitf = self._paths(sprint_dir)
        instr.write_text(build_instructions(sprint, context, scratch))
        for f in (out, exitf):                      # clear stale capture from a prior run
            if f.exists():
                f.unlink()
        prompt = (f"Read the instructions in {instr} and carry out the sprint. "
                  "Follow them exactly; do not stop to ask for confirmation.")
        cmd = (f"{self.claude_bin} -p {shlex.quote(prompt)} --dangerously-skip-permissions "
               f"> {shlex.quote(str(out))} 2>&1; echo $? > {shlex.quote(str(exitf))}")
        return launch_detached(cmd, cwd=str(repo_root) if repo_root else None)

    def is_running(self, token: str) -> bool:
        return bool(token) and is_running(token)

    def stop(self, token: str) -> None:
        if token:
            terminate_detached(token)

    def collect(self, sprint_dir: Path) -> tuple[str, str]:
        """Return (text, status) where status is 'ok' (exit 0), 'failed' (exit != 0),
        or 'interrupted' (no exit sentinel — killed/crashed; the worker relaunches)."""
        _instr, _scratch, out, exitf = self._paths(sprint_dir)
        text = out.read_text().strip() if out.exists() else ""
        if not exitf.exists():
            return text, "interrupted"
        try:
            code = int((exitf.read_text().strip() or "1"))
        except ValueError:
            code = 1
        return text, ("ok" if code == 0 else "failed")
