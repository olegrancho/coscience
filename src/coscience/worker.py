"""The Worker: supervises one long-lived agent per sprint, one beat at a time.

A beat does the smallest useful thing: if no agent is running for the claimed
sprint, launch one; if it's still running, leave it; if it has finished, collect
its result and mark the sprint done; if it died mid-run, clear it so a later beat
relaunches and the agent resumes from its scratchpad."""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import time

from coscience import feedback_harvest, usage_meter
from coscience.executor import ExecutionContext
from coscience.executor import is_running as _job_is_running
from coscience.executor import process_token, terminate_detached as _terminate
from coscience.models import BeatOutcome, Result, Sprint, SprintStatus
from coscience.substrate import Substrate

_USAGE_SCRIPT = os.path.expanduser("~/.claude/skills/usage/usage.py")
# After this many real (non-usage) failures, a sprint is marked FAILED rather than
# relaunched forever — so a deterministically-broken sprint can't burn usage.
MAX_AGENT_FAILURES = 3
# Hard cap on how long a declared detached job may run before the watchdog kills
# it, regardless of what the job itself claimed. Overridable for tests/ops.
JOB_MAX_SECONDS = float(os.environ.get("COSCIENCE_JOB_MAX_SECONDS", 7 * 24 * 3600))
# Messages a dead-on-arrival agent prints instead of doing work — must not be
# mistaken for a real result.
_USAGE_LIMIT_RE = re.compile(r"(session|usage|rate) limit|hit your .*limit|limit ·", re.I)


def _read_cost(sprint_dir) -> tuple:
    """Best-effort (cost, tokens) from the agent's cost sidecar; (None, None) if
    absent (e.g. an interrupted run, or the fake agent in tests)."""
    try:
        data = json.loads((sprint_dir / "agent.cost.json").read_text())
        return data.get("cost"), data.get("tokens")
    except (OSError, json.JSONDecodeError, ValueError):
        return None, None


def _usage_ok_from_output(out: str, now: "datetime.datetime | None" = None,
                          threshold: float = 100.0, max_cache_age: float = 900.0) -> bool:
    """Decide launch-safety from usage.py's line. Fails OPEN on a STALE cache: the
    script only serves cached data when its live fetch fails, and a cached reading
    reflects a window that may have RESET since — trusting its percentage would pin
    the pause past the reset (agents never respawning). If the cache is older than
    max_cache_age, ignore the percentage and allow launching (a dead-on-arrival
    agent is cheaply detected and retried). Fresh/live readings are trusted."""
    m = re.search(r"\[cached (\S+)\]", out)
    if m:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        try:
            fetched = datetime.datetime.strptime(
                m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
            if (now - fetched).total_seconds() > max_cache_age:
                return True
        except (ValueError, TypeError):
            return True                                   # unparseable stamp -> don't pin
    pcts = [float(x) for x in re.findall(r"(\d+)%", out)]
    return max(pcts, default=0.0) < threshold


def claude_usage_ok(threshold: float = 100.0) -> bool:
    """True if it's safe to launch a Claude agent — neither the 5-hour nor the
    weekly usage window is exhausted. Fails open: if usage can't be read, returns
    True (the worker still won't fabricate a result from a dead agent)."""
    try:
        out = subprocess.run(["python3", _USAGE_SCRIPT],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return True
    return _usage_ok_from_output(out, threshold=threshold)


class Worker:
    def __init__(self, substrate: Substrate, agent, usage_gate=None,
                 job_alive=None, terminate=None):
        self.substrate = substrate
        self.agent = agent
        # callable () -> bool; True = ok to launch. Default checks real usage.
        self._usage_gate = usage_gate
        # callable (token) -> bool; True = the detached job is still alive.
        self._job_alive = job_alive or _job_is_running
        # callable (token) -> None; stop an overrun detached job.
        self._terminate = terminate or _terminate

    def _build_context(self, sprint: Sprint) -> ExecutionContext:
        """Gather the program goal, sprint description and prior results so the
        agent knows why it is running this sprint."""
        program_title = program_goal = ""
        workdir = ""
        if sprint.program:
            try:
                prog = self.substrate.load_program(sprint.program)
                program_title, program_goal, workdir = prog.title, prog.goals, prog.workdir
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
        feedback_threads = []
        for t in sprint.threads:
            if t.get("target") != "worker" or t.get("status") != "open":
                continue
            humans = [m["text"] for m in t.get("messages", []) if m["role"] == "human"]
            if humans:
                feedback_threads.append({"thread_id": t["id"], "text": humans[-1]})
        return ExecutionContext(
            program_title=program_title, program_goal=program_goal,
            sprint_title=sprint.title, sprint_summary=sprint.summary,
            sprint_goals=sprint.goals, plan=list(sprint.plan),
            prior_results=prior,
            human_comments=[m["text"] for t in sprint.threads if t.get("target") == "worker"
                            for m in t["messages"] if m["role"] == "human"],
            feedback_threads=feedback_threads,
            # The agent's working directory: the program's project folder if it set
            # one (and it exists), else the control repo. Sprint metadata/scratchpad
            # still live in the control repo (absolute paths); only the cwd changes.
            repo_root=self._agent_cwd(workdir),
        )

    def _agent_cwd(self, workdir: str):
        if workdir:
            p = os.path.expanduser(workdir)
            if os.path.isdir(p):
                return p
        return self.substrate.repo_root

    def _claim_sprint(self):
        executing = self.substrate.iter_sprints(status=SprintStatus.EXECUTING)
        if executing:
            return executing[0]
        queued = self.substrate.iter_sprints(status=SprintStatus.QUEUED)
        if not queued:
            return None
        sprint = queued[0]
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

    def _read_job_json(self, sprint_dir):
        """Best-effort read of a declared detached job's job.json; None if
        absent, unreadable, or missing the pid the rest of the lifecycle keys
        off of."""
        f = sprint_dir / "job.json"
        if not f.is_file():
            return None
        try:
            d = json.loads(f.read_text())
            return d if isinstance(d, dict) and d.get("pid") else None
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def run_sprint_beat(self, sprint: Sprint) -> BeatOutcome:
        progress = self.substrate.load_progress(sprint.id)
        sprint_dir = self.substrate.sprint_dir(sprint.id)

        # A) sleeping on a tracked detached job (no agent runs it) — cheap check
        # only. If the agent process is (still) running, fall through to the
        # normal launched/running/collect handling below instead.
        if progress.job_token and not self.agent.is_running(progress.agent_token):
            now = time.time()
            if not self._job_alive(progress.job_token):
                progress.assess_reason = "finished"
            elif progress.job_max_seconds and progress.job_started_at is not None \
                    and now - progress.job_started_at > progress.job_max_seconds:
                self._terminate(progress.job_token)
                progress.assess_reason = "timed out"
            elif progress.job_next_wake and now >= progress.job_next_wake:
                progress.assess_reason = "wake"
            else:
                return BeatOutcome.PROGRESSED            # keep waiting; lease held
            progress.job_token = ""                       # stop tracking; assess run takes over
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: job ended ({progress.assess_reason}), assessing")
            # fall through to step 1, which launches an assess agent (assess_reason set)

        # 1) no agent yet -> launch one, unless Claude usage is exhausted
        if not progress.agent_token:
            if not self._usage_ok():
                # Don't launch into an exhausted budget — the agent would die on
                # arrival and print a limit message. Leave the sprint claimed; a
                # later beat retries once usage frees up.
                return BeatOutcome.IDLE
            ctx = self._build_context(sprint)
            token = self.agent.start(sprint, ctx, sprint_dir, ctx.repo_root)
            progress.agent_token = token
            progress.started_at = time.time()
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: agent launched")
            return BeatOutcome.PROGRESSED

        # 2) agent still working -> leave it (but harvest any feedback.out replies it
        # wrote this beat, so a human's follow-up gets answered while the agent runs)
        if self.agent.is_running(progress.agent_token):
            try:
                feedback_harvest.harvest_feedback(self.substrate, sprint.id)
            except Exception:
                pass
            return BeatOutcome.PROGRESSED

        # 3) agent ended -> collect. Only a clean exit (status 'ok') is a result;
        # a crash, kill, or usage limit must NOT be laundered into a "done" sprint.
        text, status = self.agent.collect(sprint_dir)
        # A feedback.out line written just before the agent exited would otherwise
        # never be harvested (the "still running" beat above is the only other
        # call site) — sweep once more now that the process is done.
        try:
            feedback_harvest.harvest_feedback(self.substrate, sprint.id)
        except Exception:
            pass
        progress.agent_token = ""
        # One Claude invocation just ended (clean, failed, or interrupted) — record it
        # with whatever cost/tokens the agent reported, so the dashboard can show spend.
        cost, tokens = _read_cost(sprint_dir)
        usage_meter.record_run(self.substrate.repo_root, "worker", sprint.id,
                               cost=cost, tokens=tokens, model=sprint.model)
        if status == "interrupted" or (status == "failed" and _USAGE_LIMIT_RE.search(text or "")):
            # Transient: a kill/crash mid-run (resume from scratchpad) or a usage
            # limit (the usage gate holds relaunches). Don't count it; retry later.
            why = "interrupted" if status == "interrupted" else "hit usage limit"
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: agent {why}, will retry")
            return BeatOutcome.PROGRESSED
        if status == "failed":
            # A real failure (nonzero exit). Count it; after the cap, give up so a
            # broken sprint can't relaunch forever — and record why for the PM.
            progress.failures += 1
            progress.last_error = (text or "").strip()[-600:] or "agent exited nonzero with no output"
            if progress.failures >= MAX_AGENT_FAILURES:
                sprint.status = SprintStatus.FAILED
                self.substrate.save_sprint(sprint)
                self.substrate.save_progress(progress)
                self.substrate.commit(
                    f"sprint {sprint.id}: FAILED after {progress.failures} attempts")
                return BeatOutcome.COMPLETED          # terminal -> dispatcher releases the lease
            self.substrate.save_progress(progress)
            self.substrate.commit(
                f"sprint {sprint.id}: attempt {progress.failures} failed, will retry")
            return BeatOutcome.PROGRESSED

        # A clean exit that declared a detached job takes precedence over the
        # normal done path: the agent's final message is premature (the real
        # work is still running detached), so ignore it and start sleeping on
        # the job instead.
        job = self._read_job_json(sprint_dir)
        if status == "ok" and job is not None:
            now = time.time()
            progress.job_token = process_token(int(job["pid"]))
            progress.job_out = str(job.get("out_file", ""))
            progress.job_note = str(job.get("note", ""))
            progress.job_started_at = now
            progress.job_expected_seconds = float(job.get("expected_seconds", 0) or 0)
            progress.job_next_wake = now + float(job.get("wake_after_seconds", 0) or 0)
            progress.job_max_seconds = min(float(job.get("max_seconds", 0) or 0) or JOB_MAX_SECONDS,
                                           JOB_MAX_SECONDS)
            progress.assess_reason = ""
            progress.agent_token = ""
            (sprint_dir / "job.json").unlink(missing_ok=True)     # consume it
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached job declared ({progress.job_note})")
            return BeatOutcome.PROGRESSED                          # stay executing, sleep on the job

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
        progress.job_out = progress.job_note = progress.assess_reason = ""
        progress.job_next_wake = progress.job_max_seconds = 0.0
        progress.job_started_at = None
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
