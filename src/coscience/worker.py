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
from coscience.models import BeatOutcome, Result, Sprint, SprintStatus, set_status
from coscience.substrate import Substrate

_USAGE_SCRIPT = os.path.expanduser("~/.claude/skills/usage/usage.py")
# After this many real (non-usage) failures, a sprint is marked FAILED rather than
# relaunched forever — so a deterministically-broken sprint can't burn usage.
MAX_AGENT_FAILURES = 3
# After this many consecutive clean exits with NO completion signal (neither
# finished.json nor a job.json), a sprint is marked FAILED instead of resumed
# forever — the guard against an agent that keeps ending without ever finishing.
MAX_AMBIGUOUS_EXITS = 3
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
        progress = self.substrate.load_progress(sprint.id)
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
            # Set only when this launch is resuming to check a detached job (see
            # run_sprint_beat step A); "" on a normal launch.
            assess_reason=progress.assess_reason,
            job_out=progress.job_out,
            job_note=progress.job_note,
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
        set_status(sprint, SprintStatus.EXECUTING)
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
        """Read + normalize a declared detached job's job.json. Returns a clean dict
        {pid:int, out_file, note, expected_seconds, wake_after_seconds, max_seconds}
        or None (absent / unreadable / malformed). job.json is agent-authored free-form
        JSON, so bad values (non-int pid, "5 minutes", negatives) are expected — a
        present-but-malformed file is DELETED so it can't crash every subsequent beat."""
        f = sprint_dir / "job.json"
        if not f.is_file():
            return None
        try:
            d = json.loads(f.read_text())
            if not isinstance(d, dict):
                raise ValueError("job.json is not an object")
            pid = int(d["pid"])                        # required; raises if missing/non-int
            if pid <= 0:
                raise ValueError("pid must be positive")

            def _num(key):
                try:
                    return max(0.0, float(d.get(key, 0) or 0))
                except (TypeError, ValueError):
                    return 0.0

            return {"pid": pid, "out_file": str(d.get("out_file", "")),
                    "note": str(d.get("note", "")),
                    "expected_seconds": _num("expected_seconds"),
                    "wake_after_seconds": _num("wake_after_seconds"),
                    "max_seconds": _num("max_seconds")}
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, OSError):
            try:
                f.unlink(missing_ok=True)              # drop the poison; treat as no job
            except OSError:
                pass
            return None

    def _read_finished_json(self, sprint_dir):
        """The agent's completion sentinel. Returns {"summary": str} if finished.json
        exists (the ONLY accepted done signal), else None. Presence IS the signal — a
        malformed/empty file still counts as done, with an empty summary (the result
        then falls back to the agent's final message)."""
        f = sprint_dir / "finished.json"
        if not f.is_file():
            return None
        summary = ""
        try:
            d = json.loads(f.read_text())
            if isinstance(d, dict):
                summary = str(d.get("summary", "")).strip()
        except (json.JSONDecodeError, ValueError, OSError):
            pass
        return {"summary": summary}

    def _sprint_cwd(self, sprint: Sprint):
        """The agent's working directory for a resume: the program's project folder if
        set (and present), else the control repo — same rule as a normal launch."""
        workdir = ""
        if sprint.program:
            try:
                workdir = self.substrate.load_program(sprint.program).workdir
            except OSError:
                pass
        return self._agent_cwd(workdir)

    def _nudge(self, sprint_dir) -> str:
        fj = sprint_dir / "finished.json"
        return (
            "You ended your turn without signaling completion. The platform treats "
            f"this sprint as DONE only when {fj} exists. Do exactly ONE thing now:\n"
            f'1. If the real work is genuinely FINISHED: write {fj} as '
            '{"summary": "<one-paragraph result: answer, evidence, caveats>"} and end.\n'
            "2. If a long job is still running: use the DETACHED-JOB PROTOCOL (nohup + "
            "write job.json) as your instructions describe, then end.\n"
            "3. Otherwise the work is NOT finished — continue it now and complete it.\n"
            "Do NOT merely restate that you are done without writing finished.json.")

    def _reap_job(self, progress) -> None:
        """Kill any still-tracked detached job and clear all job fields. Backstop for
        the done/failed paths so a job left tracked (e.g. an assess run that finished
        without handling a still-live job) can't be orphaned past sprint end."""
        if progress.job_token:
            try:
                self._terminate(progress.job_token)
            except Exception:
                pass
        progress.job_token = ""
        progress.job_out = progress.job_note = progress.assess_reason = ""
        progress.job_next_wake = progress.job_max_seconds = 0.0
        progress.job_started_at = None

    def run_sprint_beat(self, sprint: Sprint) -> BeatOutcome:
        progress = self.substrate.load_progress(sprint.id)
        sprint_dir = self.substrate.sprint_dir(sprint.id)

        # A) sleeping on a tracked detached job (no agent runs it) — cheap check
        # only. If the agent process is (still) running, fall through to the
        # normal launched/running/collect handling below instead.
        # Sleeping == a tracked job with no agent session running it. (Using
        # agent_token, not is_running, so that during a wake/assess run — agent_token
        # set, job_token kept — this branch stays OUT of the way and the agent is
        # collected normally in step 3.)
        if progress.job_token and not progress.agent_token:
            now = time.time()
            if not self._job_alive(progress.job_token):
                progress.assess_reason = "finished"
                progress.job_token = ""                   # job gone; nothing to track
            elif progress.job_max_seconds and progress.job_started_at is not None \
                    and now - progress.job_started_at > progress.job_max_seconds:
                self._terminate(progress.job_token)
                progress.assess_reason = "timed out"
                progress.job_token = ""                   # killed
            elif progress.job_next_wake and now >= progress.job_next_wake:
                # Job still alive — keep tracking it (watchdog stays armed) while the
                # assess run checks in. If that run finishes/fails without handling the
                # job, the done/failed path reaps it (below) so it can't be orphaned.
                progress.assess_reason = "wake"
            else:
                return BeatOutcome.PROGRESSED            # keep waiting; lease held
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
            # Drop any stale job.json / finished.json left by a prior crashed or
            # interrupted attempt, so only a signal written DURING this run's clean
            # exit is honored (else a leftover file gets misattributed to this run).
            (sprint_dir / "job.json").unlink(missing_ok=True)
            (sprint_dir / "finished.json").unlink(missing_ok=True)
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
                set_status(sprint, SprintStatus.FAILED)
                self.substrate.save_sprint(sprint)
                self._reap_job(progress)        # terminal: don't leave a job orphaned
                self.substrate.save_progress(progress)
                self.substrate.commit(
                    f"sprint {sprint.id}: FAILED after {progress.failures} attempts")
                return BeatOutcome.COMPLETED          # terminal -> dispatcher releases the lease
            self.substrate.save_progress(progress)
            self.substrate.commit(
                f"sprint {sprint.id}: attempt {progress.failures} failed, will retry")
            return BeatOutcome.PROGRESSED

        # Reaching here: a clean exit (status 'ok'). Capture the claude session id so
        # we can --resume this exact session if the agent stopped without signaling
        # completion (preserves its full context).
        sid = self.agent.read_session_id(sprint_dir)
        if sid:
            progress.agent_session_id = sid

        # 3a) declared a detached job -> the final message is premature (real work
        # still running detached): ignore it and sleep on the job instead.
        job = self._read_job_json(sprint_dir)
        if job is not None:
            now = time.time()
            progress.job_token = process_token(job["pid"])
            progress.job_out = job["out_file"]
            progress.job_note = job["note"]
            progress.job_started_at = now
            progress.job_expected_seconds = job["expected_seconds"]
            progress.job_next_wake = now + job["wake_after_seconds"]
            progress.job_max_seconds = min(job["max_seconds"] or JOB_MAX_SECONDS, JOB_MAX_SECONDS)
            progress.assess_reason = ""
            progress.agent_token = ""
            progress.ambiguous_exits = 0
            (sprint_dir / "job.json").unlink(missing_ok=True)     # consume it
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached job declared ({progress.job_note})")
            return BeatOutcome.PROGRESSED                          # stay executing, sleep on the job

        # 3b) signaled completion via finished.json -> the ONLY accepted "done".
        finished = self._read_finished_json(sprint_dir)
        if finished is not None:
            progress.ambiguous_exits = 0
            result = Result(
                id=f"{sprint.id}-result", sprint=sprint.id,
                summary=finished["summary"] or text or "(agent produced no output)",
                completed_at=time.time(),
            )
            self.substrate.save_result(result)
            set_status(sprint, SprintStatus.DONE)
            sprint.results = [result.id]
            self.substrate.save_sprint(sprint)
            progress.agent_token = ""
            self._reap_job(progress)          # kill + clear any still-tracked detached job
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: done, result {result.id}")
            return BeatOutcome.COMPLETED

        # 3c) clean exit with NO done signal. Do NOT finalize from the (premature)
        # final message. Bring the agent back in the SAME session to ask whether it
        # finished; after a few no-progress exits, give up so a stuck agent can't loop.
        progress.agent_token = ""

        # If a detached job is still tracked (only reachable via a wake-assess run that
        # ended without finishing or re-declaring), this isn't a premature completion:
        # go back to sleeping on the job so step A re-arms its watchdog next beat.
        if progress.job_token:
            self.substrate.save_progress(progress)
            self.substrate.commit(
                f"sprint {sprint.id}: assess ended without signal; back to sleeping on job")
            return BeatOutcome.PROGRESSED

        progress.assess_reason = ""    # avoid a stale assess section on a later fresh launch
        if not self._usage_ok():
            # Don't relaunch into an exhausted budget; hold WITHOUT counting and retry
            # when usage frees (also covers a deliberate near-limit wind-down that
            # ended the turn cleanly).
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: no done-signal; waiting on usage")
            return BeatOutcome.IDLE

        # Count only exits that made NO progress: a run that advanced the scratchpad
        # resets the streak, so a legitimately long multi-turn sprint is never failed —
        # only an agent that resumes and does nothing accumulates toward the cap.
        try:
            size = (sprint_dir / "scratchpad.md").stat().st_size
        except OSError:
            size = 0
        if size > progress.scratch_size:
            progress.ambiguous_exits = 1
        else:
            progress.ambiguous_exits += 1
        progress.scratch_size = size

        if progress.ambiguous_exits >= MAX_AMBIGUOUS_EXITS:
            set_status(sprint, SprintStatus.FAILED)
            progress.last_error = (
                f"worker ended {progress.ambiguous_exits} times with no progress and no "
                "completion signal (no finished.json and no job.json)")
            self.substrate.save_sprint(sprint)
            self._reap_job(progress)
            self.substrate.save_progress(progress)
            self.substrate.commit(
                f"sprint {sprint.id}: FAILED — no completion signal after "
                f"{progress.ambiguous_exits} no-progress exits")
            return BeatOutcome.COMPLETED
        if not progress.agent_session_id:
            # No session id to resume -> relaunch fresh next beat (agent resumes from
            # its scratchpad).
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: no done-signal, no session — will relaunch")
            return BeatOutcome.PROGRESSED
        token = self.agent.resume(progress.agent_session_id, sprint_dir,
                                  self._nudge(sprint_dir), sprint.model,
                                  self._sprint_cwd(sprint))
        progress.agent_token = token
        progress.started_at = time.time()
        self.substrate.save_progress(progress)
        self.substrate.commit(
            f"sprint {sprint.id}: no done-signal — resuming to ask "
            f"(attempt {progress.ambiguous_exits})")
        return BeatOutcome.PROGRESSED

    def stop_sprint(self, sprint: Sprint) -> list[str]:
        """Stop the sprint's running agent and/or its tracked detached job, and
        clear whichever was set so a later beat relaunches (the agent resumes
        from its scratchpad). Returns [sprint.id] if either was stopped, else []."""
        progress = self.substrate.load_progress(sprint.id)
        stopped = False
        if progress.agent_token:
            self.agent.stop(progress.agent_token)
            progress.agent_token = ""
            stopped = True
        if progress.job_token:
            try:
                self._terminate(progress.job_token)
            except Exception:
                pass
            progress.job_token = ""
            stopped = True
        if not stopped:
            return []
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: agent stopped")
        return [sprint.id]

    def is_yieldable(self, sprint_id: str) -> bool:
        """True if the sprint can safely yield its lease RIGHT NOW: nothing in
        flight and nothing uncollected. A non-empty `agent_token` means an agent
        run was launched and NOT yet collected — it may still be running, or it may
        have just exited leaving output (a fresh finished.json / job.json) that the
        next beat must collect. Hibernating in that window would discard a completed
        result or orphan a just-declared detached job, so it is NOT yieldable. Only
        a fully-idle sprint (token cleared) with no live job may yield."""
        progress = self.substrate.load_progress(sprint_id)
        if progress.agent_token:
            return False
        if progress.job_token and self._job_alive(progress.job_token):
            return False
        return True

    def hibernate_sprint(self, sprint: Sprint) -> None:
        """Yield at a safe point: park the sprint as HIBERNATED with its scratchpad
        intact, waiting for the dispatcher to wake it from free capacity. Any
        lingering job process group is killed; if a finished job was awaiting
        assessment, the assess pointer (assess_reason/job_out/job_note) is kept so
        the resumed run reads that output instead of starting blind."""
        progress = self.substrate.load_progress(sprint.id)
        if progress.agent_token:
            self.agent.stop(progress.agent_token)
            progress.agent_token = ""
        if progress.job_token:
            # is_yieldable guaranteed the job is not alive; kill any straggler in
            # its group, but preserve the assess context for the resumed run.
            try:
                self._terminate(progress.job_token)
            except Exception:
                pass
            progress.assess_reason = progress.assess_reason or "finished"
            progress.job_token = ""
            progress.job_started_at = None
            progress.job_next_wake = 0.0
            progress.job_max_seconds = 0.0
            progress.job_expected_seconds = 0.0
        set_status(sprint, SprintStatus.HIBERNATED, by="dispatcher", action="hibernate")
        self.substrate.save_sprint(sprint)
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: hibernated (yield for higher-priority work)")
