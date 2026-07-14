"""The production runner: one long-lived Claude agent carries out a whole sprint.

The worker writes an `instructions.md` into the sprint folder and launches
`claude -p "read instructions ... and execute" --dangerously-skip-permissions`
detached. The agent plans and does the work itself, keeps a scratchpad with
checkpoints (so it can resume if interrupted), and watches its own Claude usage.
We capture its output + exit code so the worker can collect a result when it ends."""
from __future__ import annotations

import json
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
        if context.feedback_threads:
            feedback_out = scratchpad.parent / "feedback.out"
            lines = "\n".join(f'- thread `{t["thread_id"]}`: {t["text"]}'
                              for t in context.feedback_threads)
            comments += (
                "\n\n## Open feedback threads (you may reply)\n" + lines +
                "\n\nTo answer a reviewer's feedback thread, append one JSON line "
                '{"thread_id": "<id>", "text": "<short reply>"} to '
                f"{feedback_out} (one line per reply; do not rewrite earlier lines).")
    return f"""# Sprint: {sprint.title or sprint.id}

You are an autonomous research agent. Carry out this sprint end to end, unattended.
Do the work yourself — run commands, write and read files. Do NOT ask for permission
or confirmation, and do NOT merely describe what you would do: actually do it.

You ARE the background worker for this sprint — a headless `claude -p` process the
platform launched for you. If you inspect running processes you will see your own
launcher shell and yourself running this same command: that is NOT a rival agent and
NOT a duplicate dispatch — it is you. Never stand down, ask which agent should
proceed, or refuse to work because you think another agent is running this sprint. The
platform guarantees exactly one worker per sprint. There is no human watching this
session to answer questions; asking one, or stopping to request a decision, fails the
sprint. Just do the work.

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
        model = f"--model {shlex.quote(sprint.model)} " if sprint.model else ""
        # stream-json --verbose -> agent.out becomes a live JSONL event feed (each
        # assistant turn / tool use flushed as it happens, so the dashboard can show
        # what the agent is doing right now) and ends with a `result` event carrying
        # the final message + cost/token usage, which collect() parses.
        cmd = (f"{self.claude_bin} -p {shlex.quote(prompt)} {model}"
               f"--dangerously-skip-permissions --output-format stream-json --verbose "
               f"> {shlex.quote(str(out))} 2>&1; echo $? > {shlex.quote(str(exitf))}")
        return launch_detached(cmd, cwd=str(repo_root) if repo_root else None)

    def is_running(self, token: str) -> bool:
        return bool(token) and is_running(token)

    def stop(self, token: str) -> None:
        if token:
            terminate_detached(token)

    def collect(self, sprint_dir: Path) -> tuple[str, str]:
        """Return (text, status) where status is 'ok' (exit 0), 'failed' (exit != 0),
        or 'interrupted' (no exit sentinel — killed/crashed; the worker relaunches).

        On a clean JSON envelope we unwrap the agent's final message as the result
        text and write a cost sidecar (agent.cost.json) for the dashboard; on a
        non-JSON exit (e.g. a usage-limit message) we return the raw text as-is so
        the worker's limit detection still fires."""
        _instr, _scratch, out, exitf = self._paths(sprint_dir)
        raw = out.read_text().strip() if out.exists() else ""
        if not exitf.exists():
            return raw, "interrupted"
        try:
            code = int((exitf.read_text().strip() or "1"))
        except ValueError:
            code = 1
        text = self._unwrap_envelope(raw, sprint_dir)
        return text, ("ok" if code == 0 else "failed")

    @staticmethod
    def _unwrap_envelope(raw: str, sprint_dir: Path) -> str:
        """Scan the JSONL event stream for the final `result` event: return its
        message text and write a cost sidecar. If no such event is present (e.g. a
        usage-limit message instead of a stream), return the raw text unchanged so
        the worker's limit detection still fires."""
        result = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(ev, dict) and ev.get("type") == "result" and "result" in ev:
                result = ev                              # keep the last one
        if result is None:
            return raw
        usage = result.get("usage") or {}
        tokens = sum(int(usage.get(k, 0) or 0) for k in (
            "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens"))
        sidecar = {"cost": result.get("total_cost_usd"), "tokens": tokens,
                   "turns": result.get("num_turns"), "duration_ms": result.get("duration_ms")}
        try:
            (sprint_dir / "agent.cost.json").write_text(json.dumps(sidecar))
        except OSError:
            pass
        return str(result.get("result") or "")


def read_activity(sprint_dir: Path, fresh_within: float = 90.0,
                  now: float | None = None) -> dict | None:
    """What the agent is doing right now, from the tail of its JSONL event feed.

    Returns {label, active, at} where `label` is a short phrase ('using Bash',
    'thinking', 'finished'), `active` is True if the feed was written to recently
    (the process is alive and producing), and `at` is the feed's mtime. Returns
    None if there's no feed yet. Best-effort — never raises."""
    import time as _time
    out = sprint_dir / "agent.out"
    try:
        mtime = out.stat().st_mtime
        raw = out.read_text()
    except OSError:
        return None
    now = _time.time() if now is None else now
    label = "starting"
    for line in raw.splitlines():                        # last meaningful event wins
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        lab = _event_label(ev)
        if lab:
            label = lab
    return {"label": label, "active": (now - mtime) <= fresh_within, "at": mtime}


def _event_label(ev: dict) -> str:
    """A short human label for one stream-json event, or '' to ignore it."""
    if not isinstance(ev, dict):
        return ""
    kind = ev.get("type")
    if kind == "result":
        return "finished"
    if kind == "system":
        return "starting"
    if kind == "assistant":
        for block in (ev.get("message") or {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "a tool")
                inp = block.get("input") or {}
                target = inp.get("file_path") or inp.get("path") or inp.get("pattern")
                if name in ("Bash",) and inp.get("command"):
                    return f"running: {str(inp['command']).splitlines()[0][:60]}"
                return f"using {name}" + (f" · {Path(str(target)).name}" if target else "")
        return "thinking"
    if kind == "user":                                   # a tool returned -> agent will react
        return "reading tool output"
    return ""
