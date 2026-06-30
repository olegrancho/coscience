"""The Worker: supervises one long-lived agent per sprint, one beat at a time.

A beat does the smallest useful thing: if no agent is running for the claimed
sprint, launch one; if it's still running, leave it; if it has finished, collect
its result and mark the sprint done; if it died mid-run, clear it so a later beat
relaunches and the agent resumes from its scratchpad."""
from __future__ import annotations

import os
import re
import subprocess
import time

from coscience.executor import ExecutionContext
from coscience.models import BeatOutcome, Result, Sprint, SprintStatus
from coscience.substrate import Substrate

_USAGE_SCRIPT = os.path.expanduser("~/.claude/skills/usage/usage.py")
# Messages a dead-on-arrival agent prints instead of doing work — must not be
# mistaken for a real result.
_USAGE_LIMIT_RE = re.compile(r"(session|usage|rate) limit|hit your .*limit|limit ·", re.I)


def claude_usage_ok(threshold: float = 100.0) -> bool:
    """True if it's safe to launch a Claude agent — neither the 5-hour nor the
    weekly usage window is exhausted. Fails open: if usage can't be read, returns
    True (the worker still won't fabricate a result from a dead agent)."""
    try:
        out = subprocess.run(["python3", _USAGE_SCRIPT],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return True
    pcts = [float(x) for x in re.findall(r"(\d+)%", out)]
    return max(pcts, default=0.0) < threshold


class Worker:
    def __init__(self, substrate: Substrate, agent, usage_gate=None):
        self.substrate = substrate
        self.agent = agent
        # callable () -> bool; True = ok to launch. Default checks real usage.
        self._usage_gate = usage_gate

    def _build_context(self, sprint: Sprint) -> ExecutionContext:
        """Gather the program goal, sprint description and prior results so the
        agent knows why it is running this sprint."""
        program_title = program_goal = ""
        if sprint.program:
            try:
                prog = self.substrate.load_program(sprint.program)
                program_title, program_goal = prog.title, prog.goals
            except OSError:
                pass
        prior: list[str] = []
        for s in self.substrate.iter_sprints(status=SprintStatus.DONE):
            if s.program != sprint.program or s.id == sprint.id:
                continue
            for rid in s.results:
                try:
                    summary = self.substrate.load_result(rid).summary.strip()
                except OSError:
                    continue
                prior.append(f"## {s.title or s.id}\n{summary[:1000]}")
        return ExecutionContext(
            program_title=program_title, program_goal=program_goal,
            sprint_title=sprint.title, sprint_summary=sprint.summary,
            sprint_goals=sprint.goals, plan=list(sprint.plan),
            prior_results=prior, repo_root=self.substrate.repo_root,
        )

    def _claim_sprint(self):
        executing = self.substrate.iter_sprints(status=SprintStatus.EXECUTING)
        if executing:
            return executing[0]
        approved = self.substrate.iter_sprints(status=SprintStatus.APPROVED)
        if not approved:
            return None
        sprint = approved[0]
        sprint.status = SprintStatus.EXECUTING
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint.id}: start executing")
        return sprint

    def run_one_beat(self) -> BeatOutcome:
        sprint = self._claim_sprint()
        if sprint is None:
            return BeatOutcome.IDLE
        return self.run_sprint_beat(sprint)

    def agent_running(self, sprint_id: str) -> bool:
        return self.agent.is_running(self.substrate.load_progress(sprint_id).agent_token)

    def _usage_ok(self) -> bool:
        return (self._usage_gate or claude_usage_ok)()

    def run_sprint_beat(self, sprint: Sprint) -> BeatOutcome:
        progress = self.substrate.load_progress(sprint.id)
        sprint_dir = self.substrate.sprint_dir(sprint.id)

        # 1) no agent yet -> launch one, unless Claude usage is exhausted
        if not progress.agent_token:
            if not self._usage_ok():
                # Don't launch into an exhausted budget — the agent would die on
                # arrival and print a limit message. Leave the sprint claimed; a
                # later beat retries once usage frees up.
                return BeatOutcome.IDLE
            token = self.agent.start(sprint, self._build_context(sprint),
                                     sprint_dir, self.substrate.repo_root)
            progress.agent_token = token
            progress.started_at = time.time()
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: agent launched")
            return BeatOutcome.PROGRESSED

        # 2) agent still working -> leave it
        if self.agent.is_running(progress.agent_token):
            return BeatOutcome.PROGRESSED

        # 3) agent ended -> collect. Only a clean exit (status 'ok') is a result;
        # a crash, kill, or usage limit must NOT be laundered into a "done" sprint.
        text, status = self.agent.collect(sprint_dir)
        if status != "ok":
            why = "interrupted" if status == "interrupted" else (
                "hit usage limit" if _USAGE_LIMIT_RE.search(text or "") else "failed")
            # Clear the token so a later beat relaunches (the agent resumes from
            # its scratchpad). The sprint stays EXECUTING — it is not done.
            progress.agent_token = ""
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: agent {why}, will retry")
            return BeatOutcome.PROGRESSED

        result = Result(
            id=f"{sprint.id}-result", sprint=sprint.id,
            summary=text or "(agent produced no output)",
            completed_at=time.time(),
        )
        self.substrate.save_result(result)
        sprint.status = SprintStatus.DONE
        sprint.results = [result.id]
        self.substrate.save_sprint(sprint)
        progress.agent_token = ""
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: done, result {result.id}")
        return BeatOutcome.COMPLETED

    def stop_sprint(self, sprint: Sprint) -> list[str]:
        """Stop the sprint's running agent and clear it so a later beat relaunches
        (the agent resumes from its scratchpad). Returns [sprint.id] if one was
        stopped, else []."""
        progress = self.substrate.load_progress(sprint.id)
        if not progress.agent_token:
            return []
        self.agent.stop(progress.agent_token)
        progress.agent_token = ""
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: agent stopped")
        return [sprint.id]
