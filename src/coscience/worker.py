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
import time
from pathlib import Path

from coscience import artifacts, escalation, feedback_harvest, host_health, remote_exec, usage_meter
from coscience.executor import ExecutionContext
from coscience.executor import is_running as _job_is_running
from coscience.executor import process_token, terminate_detached as _terminate
from coscience.host_probe import subprocess_runner
from coscience.models import BeatOutcome, Result, Sprint, SprintStatus, set_status
from coscience.pause import is_paused
from coscience.resources import LOCAL
from coscience.substrate import Substrate

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


def _could_not_stop_line(host: str) -> str:
    """A remote job the platform could not stop must not be forgotten silently —
    surfaced in `collect_note` so a human sees it when they look at the sprint."""
    return (f"The platform could not stop the job on {host} (the host "
            "could not be asked, the job's identity was never read, or the pid is "
            "not the leader of its own process group — launch with setsid). Check "
            "whether it is still running and stop it yourself.")


def _nothing_to_collect_line(host: str) -> str:
    """A stopped job that copied nothing back says why (O19): silence reads as work
    thrown away, when in fact there was either nothing declared or nothing to move."""
    if not host:
        return ("The job was stopped. It ran on this machine, so whatever it wrote is "
                "already in the sprint folder.")
    return (f"The job on {host} was stopped. It declared no paths to copy back, so "
            f"anything it wrote is still on {host} and was not collected.")


def _read_cost(sprint_dir) -> dict:
    """Best-effort usage from the agent's cost sidecar; {} if absent (an interrupted
    run, or the fake agent in tests). Returns the whole sidecar so the per-component
    split and `turns` reach the ledger — the executor has written `turns` since it
    was added, and this function used to drop it on the floor."""
    try:
        data = json.loads((sprint_dir / "agent.cost.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _usage_ok_from_limits(limits, threshold: float = 100.0,
                          weekly_threshold: float | None = None) -> bool | None:
    """Decide launch-safety from `usage_meter.read_limits()`: the window percentages
    and nothing else. None means "can't tell from this" — no reading, or one carrying
    no windows — and the caller falls back.

    The reading's status field (e.g. "allowed_warning") is deliberately not read: it
    is advice nobody asked the platform to act on, and acting on it blocked chat and
    the PM loop at 30% of the 5-hour window. A call Claude actually refuses shows up
    as a full window and as that call's own rate-limited outcome."""
    if not limits:
        return None
    windows = limits.get("windows") or {}
    if not windows:
        return None
    if weekly_threshold is None:
        weekly_threshold = threshold
    return (windows.get("5h", {}).get("pct", 0) < threshold
            and windows.get("week", {}).get("pct", 0) < weekly_threshold)


def _usage_ok_from_output(out: str, now: "datetime.datetime | None" = None,
                          threshold: float = 100.0, weekly_threshold: float | None = None,
                          max_cache_age: float = 900.0) -> bool:
    """Decide launch-safety from usage.py's line. Fails OPEN on a STALE cache: the
    script only serves cached data when its live fetch fails, and a cached reading
    reflects a window that may have RESET since — trusting its percentage would pin
    the pause past the reset (agents never respawning). If the cache is older than
    max_cache_age, ignore the percentage and allow launching (a dead-on-arrival
    agent is cheaply detected and retried). Fresh/live readings are trusted.

    `threshold` gates the 5-hour window. `weekly_threshold` (defaults to
    `threshold` when None) gates the weekly window independently — agent launches
    can tolerate higher weekly usage than the 5h window."""
    if weekly_threshold is None:
        weekly_threshold = threshold
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
    # Parse per-window percentages: "5h: 53% ... | week: 91% ..."
    m5 = re.search(r"5h:\s*(\d+)%", out)
    mw = re.search(r"week:\s*(\d+)%", out)
    if m5 or mw:
        pct_5h = float(m5.group(1)) if m5 else 0.0
        pct_wk = float(mw.group(1)) if mw else 0.0
        return pct_5h < threshold and pct_wk < weekly_threshold
    # Fallback: unlabeled percentages — apply the stricter threshold to max.
    pcts = [float(x) for x in re.findall(r"(\d+)%", out)]
    return max(pcts, default=0.0) < min(threshold, weekly_threshold)


# Usage is a fixed subscription window, not a bill: the scarce thing is the share
# left for a human who wants a chat or a forced replan. Autonomous loops stand down
# early and leave the top band for them; human-triggered paths keep the full 100.
AUTONOMOUS_THRESHOLD = 80.0     # PM loop beats
WORKER_THRESHOLD = 90.0         # worker agent launches
WEEKLY_WORKER_THRESHOLD = 99.0  # weekly window is less scarce — don't block agents over it


def claude_usage_ok(threshold: float = 100.0, *, weekly_threshold: float | None = None,
                    fail_open: bool = True, repo_root=None) -> bool:
    """True if it's safe to launch a Claude agent at this threshold — the 5-hour
    window hasn't passed `threshold` and the weekly window hasn't passed
    `weekly_threshold`.

    Answered from the rate-limit reading our own runs recorded whenever there is a
    recent one, which costs nothing. The usage script is the fallback for a host
    that hasn't run Claude lately; it hits an endpoint that rate-limits callers, so
    a gate on a 5-second loop must not reach it every time.

    `fail_open` decides what an unreadable usage script means:
    True for human-triggered work (never block a person on a missing dotfile),
    False for autonomous loops (an unmetered loop is exactly what burns a window
    unattended).

    `repo_root` enables the human pause check, and is tested FIRST: a paused platform
    starts no new Claude session whatever the windows say, and polling usage.py to
    learn that would be wasted work. None (the default) skips the check, for callers
    that hold no substrate."""
    if repo_root is not None and is_paused(repo_root):
        return False
    decided = _usage_ok_from_limits(usage_meter.read_limits(), threshold=threshold,
                                    weekly_threshold=weekly_threshold)
    if decided is not None:
        return decided
    out = usage_meter.usage_output()
    if out is None:
        return fail_open
    return _usage_ok_from_output(out, threshold=threshold, weekly_threshold=weekly_threshold)


class _NoSlots:
    """Uncapped: every acquire succeeds and releases are forgotten."""

    def release(self, sprint_id: str) -> None:
        pass

    def acquire(self, sprint_id: str) -> bool:
        return True

    def gpus(self, sprint_id: str) -> tuple[list[int], float | None]:
        return [], None

    def host(self, sprint_id: str) -> dict:
        return {"name": "local", "ssh": "", "run_root": "", "facts": "", "notes": ""}

    def ssh_for(self, host_name: str) -> str:
        return ""

    def host_quiet(self, host_name: str) -> bool:
        return False


_NO_SLOTS = _NoSlots()


class Worker:
    def __init__(self, substrate: Substrate, agent, usage_gate=None,
                 job_alive=None, terminate=None, slots=None, runner=None):
        self.substrate = substrate
        self.agent = agent
        # The dispatcher's worker-slot handle: .release(id) / .acquire(id) -> bool.
        # A sprint asleep on a detached job runs no agent process, so it must not
        # hold the slot that bounds how many agents run at once — p5-c26 held the
        # substrate's only one for 15h while a GPU job ran. None (tests, and any
        # non-dispatcher caller) means uncapped, exactly as before.
        self._slots = slots or _NO_SLOTS
        # callable () -> bool; True = ok to launch. Default checks real usage.
        self._usage_gate = usage_gate
        # (argv, stdin, timeout) -> (code, out, err), for remote job control over ssh.
        self._runner = runner or subprocess_runner
        # callable (token) -> bool; True = the detached job is still alive.
        self._job_alive = job_alive or self._default_job_alive
        # callable (token) -> None; stop an overrun detached job.
        self._terminate = terminate or self._default_terminate
        # "" (never a remote token seen) | "alive"/"gone"/"lost"/"unknown" — the
        # remote job_state the last _default_job_alive call read, so the caller can
        # tell "gone" from "lost" without re-parsing the token.
        self._last_job_state = ""
        # Whether the most recent self._terminate() call actually stopped the job
        # (True for a local token, and for a remote call that succeeded); False when
        # a remote host could not be asked or the token had no identity to verify.
        self._last_terminate_ok = True

    def _default_job_alive(self, token: str) -> bool:
        """A local job by /proc; a remote job over ssh. An unreachable host counts as
        alive: a job is never declared dead because its host could not be asked."""
        remote = remote_exec.parse_token(token)
        if remote is None:
            self._last_job_state = ""
            return _job_is_running(token)
        ssh = self._slots.ssh_for(remote.host)
        state = remote_exec.job_state(ssh, remote, runner=self._runner) if ssh else "unknown"
        self._last_job_state = state
        return state in ("alive", "unknown")

    def _default_terminate(self, token: str) -> None:
        remote = remote_exec.parse_token(token)
        if remote is None:
            _terminate(token)
            self._last_terminate_ok = True
            return
        ssh = self._slots.ssh_for(remote.host)
        if ssh:
            self._last_terminate_ok = remote_exec.terminate(ssh, remote, runner=self._runner)
        else:
            self._last_terminate_ok = False

    def _collect_job(self, progress, sprint_dir, *, stopping: bool = False) -> None:
        """Copy a remote job's declared outputs into the sprint's `collected/` folder
        before the agent is woken, and leave a note saying exactly what was copied.

        `stopping` says the sprint is ending instead of being woken, so the note is
        written for the human who stopped it — and a job with nothing to copy says so
        rather than returning silently, because from the outside "no note" and "the
        work was thrown away" look the same (O19)."""
        if not progress.job_host or not progress.job_collect:
            if stopping and progress.job_token:
                progress.collect_note = _nothing_to_collect_line(progress.job_host)
            return
        ssh = self._slots.ssh_for(progress.job_host)
        dest = Path(sprint_dir) / "collected"
        if not ssh:
            progress.collect_note = (f"Nothing was copied back: host {progress.job_host} is no "
                                     "longer in the pool.")
            return
        # The substrate commit that follows this beat is `git add -A`: without this,
        # a collected checkpoint or large log would land in the substrate's history.
        # A folder whose own .gitignore is exactly "*" is ignored entirely, including
        # the .gitignore itself.
        dest.mkdir(parents=True, exist_ok=True)
        gitignore = dest / ".gitignore"
        try:
            already = gitignore.read_text() == "*\n"
        except OSError:
            already = False
        if not already:
            gitignore.write_text("*\n")
        results = remote_exec.collect(ssh, progress.job_collect, dest, runner=self._runner)
        stamp = time.strftime("%H:%M")

        def _line(r: dict) -> str:
            if not r["ok"]:
                return f"- {r['path']} was NOT copied: {r['detail']}"
            base = f"- {r['path']} → {dest}/{Path(r['path']).name} (at {stamp})"
            return f"{base} — {r['detail']}" if r["detail"] else base

        lines = [_line(r) for r in results]
        lead = (f"The sprint was stopped. After stopping the job, the platform copied these paths "
                f"from {progress.job_host} into {dest}. Nothing else was copied."
                if stopping else
                f"Before waking you, the platform copied these paths from {progress.job_host}. Read "
                "the results there; you do not need to copy them yourself. Nothing else was copied.")
        progress.collect_note = lead + "\n" + "\n".join(lines)

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
        artifact_specs: list[dict] = []
        if sprint.program:
            for aid in artifacts.sprint_aids(sprint):
                work_path = self.substrate.artifact_dir(sprint.program, aid) / "work"
                try:
                    kind = self.substrate.load_artifact(sprint.program, aid).kind
                except OSError:
                    kind = next((str(c.get("kind") or "md")
                                 for c in sprint.artifacts_create
                                 if str(c.get("aid") or "") == aid), "md")
                artifact_specs.append({"aid": aid, "kind": kind, "work_path": str(work_path)})
        gpu_devices, gpu_vram_gb = self._slots.gpus(sprint.id)
        host = self._slots.host(sprint.id)
        host_run_dir = f"{host['run_root'].rstrip('/')}/{sprint.id}" if host["ssh"] and host["run_root"] else ""
        program_host_notes = ""
        if sprint.program:
            try:
                program_host_notes = self.substrate.load_host_note(
                    sprint.program, host["name"] or LOCAL)
            except (OSError, ValueError):
                pass
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
            artifacts=artifact_specs,
            gpu_devices=gpu_devices,
            gpu_vram_gb=gpu_vram_gb,
            host_name=host["name"], host_ssh=host["ssh"], host_run_dir=host_run_dir,
            host_facts=host["facts"], host_notes=host["notes"],
            program_host_notes=program_host_notes,
            collect_note=progress.collect_note,
            resume_note=progress.resume_note,
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
        return (self._usage_gate or
                (lambda: claude_usage_ok(WORKER_THRESHOLD,
                                         weekly_threshold=WEEKLY_WORKER_THRESHOLD,
                                         fail_open=False,
                                         repo_root=self.substrate.repo_root)))()

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
                    "max_seconds": _num("max_seconds"),
                    "host": str(d.get("host") or ""),
                    "collect": [str(p) for p in (d.get("collect") or []) if isinstance(p, str)]
                               if isinstance(d.get("collect"), list) else []}
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, OSError):
            try:
                f.unlink(missing_ok=True)              # drop the poison; treat as no job
            except OSError:
                pass
            return None

    def _read_finished_json(self, sprint_dir):
        """The agent's completion sentinel. Returns {"summary": str, "host_notes": str}
        if finished.json exists (the ONLY accepted done signal), else None. Presence IS
        the signal — a malformed/empty file still counts as done, with an empty summary
        (the result then falls back to the agent's final message). A host_notes that is
        missing or not a string is no report: completion never depends on it."""
        f = sprint_dir / "finished.json"
        if not f.is_file():
            return None
        summary = host_notes = ""
        try:
            d = json.loads(f.read_text())
            if isinstance(d, dict):
                summary = str(d.get("summary", "")).strip()
                raw = d.get("host_notes")
                host_notes = raw.strip() if isinstance(raw, str) else ""
        except (json.JSONDecodeError, ValueError, OSError):
            pass
        return {"summary": summary, "host_notes": host_notes}

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

    def _nudge(self, sprint_dir, job_refusal: str = "") -> str:
        fj = sprint_dir / "finished.json"
        why = (f"Your last job.json was refused, so nothing is being tracked: {job_refusal}\n\n"
               if job_refusal else "")
        return (
            why +
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
        progress.job_host = ""
        progress.job_collect = []
        progress.job_out = progress.job_note = progress.assess_reason = ""
        progress.job_next_wake = progress.job_max_seconds = 0.0
        progress.job_started_at = None

    def _declare_job(self, sprint: Sprint, progress, sprint_dir):
        """Read + validate a just-exited run's job.json and, if accepted, record it
        onto `progress` (job_token/job_host/job_collect/...) and consume the file.

        Returns (declared, state, job_refusal): `declared` is False when there was no
        job.json, or it was refused for naming a host this sprint does not hold (the
        file is unlinked either way; `job_refusal` explains a refusal). When declared,
        `state` is "alive"/"gone"/"unknown" as resolved by identity verification.

        Shared by the normal post-exit path (3a: sleeps on the job, and nudges the
        agent if it turns out to be "gone") and the escalation path (tracks it
        identically but does neither — see run_sprint_beat)."""
        job = self._read_job_json(sprint_dir)
        job_refusal = ""
        if job is not None:
            held = self._slots.host(sprint.id)["name"]
            if job["host"] in ("", "local") and held != LOCAL:
                # The sprint's lease is on a remote host; an agent that omitted
                # `host` (or wrote "local") still ran its job there, not on this
                # machine. Treat it as declared on the held host so its pid is never
                # mistaken for a local process (a live pid here could belong to
                # someone else entirely) and watchdog/cancel/reap ask the real host.
                job = {**job, "host": held}
            elif job["host"] not in ("", "local") and job["host"] != held:
                # A job on a host this sprint does not hold could be neither watched
                # nor stopped: refuse it and say why — and, if this run ends up being
                # brought back with a nudge, tell IT why too (M1).
                (sprint_dir / "job.json").unlink(missing_ok=True)
                job_refusal = (f"job.json named host {job['host']!r} but this sprint "
                               f"runs on {held!r}")
                progress.last_error = job_refusal
                job = None
        if job is None:
            return False, "", job_refusal
        now = time.time()
        job_host = "" if job["host"] in ("", "local") else job["host"]
        state = "alive"
        if job_host:
            ssh = self._slots.ssh_for(job_host)
            if ssh:
                new_token, state = remote_exec.make_token(
                    job_host, ssh, job["pid"], runner=self._runner)
            else:
                # The held host has no usable ssh target (removed from the pool,
                # or an invalid ssh value slipping past parse-time validation —
                # Fix C): never hand a "" target to make_token/ssh_argv, which
                # would raise mid-beat and stall every later lease this cycle.
                new_token = str(remote_exec.RemoteToken(job_host, job["pid"], "", ""))
                state = "unknown"
            prev = remote_exec.parse_token(progress.job_token)
            if state == "unknown" and prev is not None and prev.host == job_host \
                    and prev.pid == job["pid"]:
                # Same job re-declared (e.g. the "wake" flow, which never clears
                # job_token) while the host happens to be unreachable right now:
                # keep the token we already trust — its starttime/boot_id are
                # what terminate() needs — instead of downgrading to a fresh,
                # identity-less one.
                pass
            else:
                progress.job_token = new_token
        else:
            progress.job_token = process_token(job["pid"])
        progress.job_host = job_host
        progress.job_collect = job["collect"] if job_host else []
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
        return True, state, job_refusal

    def run_sprint_beat(self, sprint: Sprint) -> BeatOutcome:
        progress = self.substrate.load_progress(sprint.id)
        sprint_dir = self.substrate.sprint_dir(sprint.id)

        # A human asked to stop this sprint — honored FIRST, before the sleeping-
        # on-a-job branch below, so a sprint asleep on a remote job is stopped too.
        # Mirrors run_escalated_beat's stop block: a human stop of running work is
        # a cancel, not a failure.
        if progress.stop_requested:
            last_error = "stopped by a human"
            try:
                self.stop_sprint(sprint, collect=True)
            except Exception as exc:
                last_error += f" (stopping it also failed: {exc})"
            sprint = self.substrate.load_sprint(sprint.id)        # reload: stop_sprint may
            progress = self.substrate.load_progress(sprint.id)    # have taken a while
            if sprint.status not in (SprintStatus.EXECUTING, SprintStatus.HIBERNATED):
                # Someone else already moved this sprint on (e.g. a second dispatcher
                # instance beat us to it) while stop_sprint ran — the stop we just did
                # is harmless (idempotent once nothing is left running), but writing
                # CANCELED over whatever it is now would clobber real state. Both
                # EXECUTING and HIBERNATED are accepted: this beat runs for either
                # (a hibernated sprint holds no lease, so the dispatcher reaches it
                # through its own leaseless-stop loop, not the per-lease one).
                return BeatOutcome.PROGRESSED
            if progress.collect_note:
                last_error += f"; {progress.collect_note}"
            progress.last_error = last_error
            progress.stop_requested = False
            progress.escalation = {}
            self._reap_job(progress)
            set_status(sprint, SprintStatus.CANCELED)
            artifacts.release_for_sprint(self.substrate, sprint, time.time())
            self.substrate.save_sprint(sprint)
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: CANCELED after a human stop")
            return BeatOutcome.COMPLETED

        # A) sleeping on a tracked detached job (no agent runs it) — cheap check
        # only. If the agent process is (still) running, fall through to the
        # normal launched/running/collect handling below instead.
        # Sleeping == a tracked job with no agent session running it. (Using
        # agent_token, not is_running, so that during a wake/assess run — agent_token
        # set, job_token kept — this branch stays OUT of the way and the agent is
        # collected normally in step 3.)
        if progress.job_token and not progress.agent_token:
            now = time.time()
            if progress.job_host and self._slots.host_quiet(progress.job_host) \
                    and sprint.status == SprintStatus.EXECUTING:
                # The host this sprint sleeps on has stopped answering the platform's
                # health checks — the job may still be running, but nobody can watch
                # or collect it. Ask for help instead of guessing. Guarded on the
                # sprint's own status (this branch only ever runs for an EXECUTING
                # sprint, so it can never already have an open escalation) rather
                # than on `progress.escalation` — a stale escalation dict left over
                # from an earlier, already-answered one must not block a new one.
                escalation.raise_escalation(self.substrate, sprint, progress, {
                    "by": "dispatcher", "host": progress.job_host,
                    "what": (f"{progress.job_host} has not answered the platform's checks for "
                             f"{int(host_health.QUIET_AFTER // 60)} minutes while this sprint sleeps "
                             f"on its job ({progress.job_note or 'job'}); the job's state is unknown"),
                    "tried": "the platform's regular host checks",
                    "may_have_broken_something": False,
                    "needs": "a working host, or confirmation the job is still running",
                }, now)
                self._slots.release(sprint.id)
                self.substrate.commit(
                    f"sprint {sprint.id}: escalated by the platform ({progress.job_host} quiet)")
                return BeatOutcome.PROGRESSED
            if not self._job_alive(progress.job_token):
                progress.assess_reason = "lost" if self._last_job_state == "lost" else "finished"
                self._collect_job(progress, sprint_dir)
                progress.job_token = ""                   # job gone; nothing to track
                progress.job_host = ""
                progress.job_collect = []
            elif progress.job_max_seconds and progress.job_started_at is not None \
                    and now - progress.job_started_at > progress.job_max_seconds:
                self._terminate(progress.job_token)
                self._collect_job(progress, sprint_dir)
                if not self._last_terminate_ok:
                    line = _could_not_stop_line(progress.job_host)
                    progress.collect_note = (f"{progress.collect_note}\n{line}"
                                             if progress.collect_note else line)
                progress.assess_reason = "timed out"
                progress.job_token = ""                   # killed
                progress.job_host = ""
                progress.job_collect = []
            elif progress.job_next_wake and now >= progress.job_next_wake:
                # Job still alive — keep tracking it (watchdog stays armed) while the
                # assess run checks in. If that run finishes/fails without handling the
                # job, the done/failed path reaps it (below) so it can't be orphaned.
                self._collect_job(progress, sprint_dir)
                progress.assess_reason = "wake"
            else:
                # Waiting on the job, not on Claude. Keep the lease (it holds the
                # cpu/gpu the job is really using) but give back the worker slot,
                # which is charged for an agent process that is not running.
                self._slots.release(sprint.id)
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
            # Same shape, for compute rather than budget: a sprint that slept
            # through a detached job gave its worker slot back, and launching is
            # what needs it again. If another sprint took it meanwhile this one
            # waits exactly as a queued sprint waits — job still running, lease
            # still held, next beat tries again.
            if not self._slots.acquire(sprint.id):
                return BeatOutcome.PROGRESSED
            # Drop any stale job.json / finished.json / escalate.json left by a prior
            # crashed, interrupted or failed attempt, so only a signal written DURING
            # this run's exit is honored (else a leftover file gets misattributed to
            # this run — a failed run's escalate.json would otherwise escalate a later,
            # unrelated clean run).
            (sprint_dir / "job.json").unlink(missing_ok=True)
            (sprint_dir / "finished.json").unlink(missing_ok=True)
            (sprint_dir / "escalate.json").unlink(missing_ok=True)
            ctx = self._build_context(sprint)
            token = self.agent.start(sprint, ctx, sprint_dir, ctx.repo_root)
            progress.agent_token = token
            # Remember the cards this agent was told it holds, so a dispatcher outage
            # that outlives the lease TTL can re-grant this sprint the SAME cards
            # instead of handing them out from scratch (see Ledger.pick_gpus prefer).
            progress.gpu_devices = list(ctx.gpu_devices)
            progress.host = ctx.host_name
            progress.collect_note = ""      # the note has now been handed to this run
            progress.resume_note = ""       # ditto for an escalation answer's note
            # Opened at launch so a killed agent still leaves a row; `calls()`
            # infers `lost` for a start that never gets an end.
            progress.agent_call = usage_meter.start_call(
                self.substrate.repo_root, "worker", program=sprint.program or "",
                sprint=sprint.id, model=sprint.model,
                limits=usage_meter.current_window(), token=str(token))
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
        # One Claude invocation just ended (clean, failed, or interrupted) — close its
        # row with whatever cost/tokens the agent reported, so Compute shows the spend.
        sidecar = _read_cost(sprint_dir)
        hit_limit = bool(_USAGE_LIMIT_RE.search(text or ""))
        call_status = ("interrupted" if status == "interrupted"
                       else "rate-limited" if (status == "failed" and hit_limit)
                       else status)
        if progress.agent_call:
            usage_meter.finish_call(
                self.substrate.repo_root, progress.agent_call, status=call_status,
                cost=sidecar.get("cost"), tokens=sidecar.get("tokens"),
                turns=sidecar.get("turns"), usage=sidecar.get("usage"),
                model=sprint.model,
                # The run's own stream over the usage script: exact, free, and it
                # cannot be a stale cache. `current_window` stays as the fallback
                # for a run that ended without ever reporting a window.
                limits=sidecar.get("limits") or usage_meter.current_window(),
                limits_before=sidecar.get("limits_before"))
            progress.agent_call = ""
        else:
            usage_meter.record_run(self.substrate.repo_root, "worker", sprint.id,
                                   cost=sidecar.get("cost"), tokens=sidecar.get("tokens"),
                                   turns=sidecar.get("turns"), usage=sidecar.get("usage"),
                                   model=sprint.model, ok=(status == "ok"))
        if status == "interrupted" or (status == "failed" and _USAGE_LIMIT_RE.search(text or "")):
            # Transient: a kill/crash mid-run (resume from scratchpad) or a usage
            # limit (the usage gate holds relaunches). Don't count it; retry later.
            why = "interrupted" if status == "interrupted" else "hit usage limit"
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: agent {why}, will retry")
            return BeatOutcome.PROGRESSED

        # An agent that wrote escalate.json is asking for help — checked BEFORE the
        # failure branch below, so a real (nonzero-exit) failure that escalated is
        # never counted toward the retry cap: it asked for help instead of just
        # dying. Hold the sprint with its lease; track anything it declared as a
        # detached job in the same turn exactly as 3a would (so it isn't orphaned),
        # but without sleeping on it or nudging about a "gone" job — nobody is being
        # woken. A finished.json in the same turn is not read: the escalation wins
        # over a completion claim. Never relaunch until the PM or a human answers.
        record = escalation.read_escalate_json(sprint_dir)
        if record is not None:
            (sprint_dir / "escalate.json").unlink(missing_ok=True)
            progress.agent_token = ""
            self._declare_job(sprint, progress, sprint_dir)
            held = self._slots.host(sprint.id)["name"]
            host = progress.job_host or ("" if held == LOCAL else held)
            escalation.raise_escalation(self.substrate, sprint, progress,
                                        {**record, "by": "agent", "host": host}, time.time())
            self._slots.release(sprint.id)
            self.substrate.commit(f"sprint {sprint.id}: escalated by the agent")
            return BeatOutcome.PROGRESSED

        if status == "failed":
            # A real failure (nonzero exit). Count it; after the cap, give up so a
            # broken sprint can't relaunch forever — and record why for the PM.
            progress.failures += 1
            progress.last_error = (text or "").strip()[-600:] or "agent exited nonzero with no output"
            if progress.failures >= MAX_AGENT_FAILURES:
                set_status(sprint, SprintStatus.FAILED)
                artifacts.release_for_sprint(self.substrate, sprint, time.time())
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

        # Reaching here: a clean exit (status 'ok'), no escalation. Capture the claude
        # session id so we can --resume this exact session if the agent stopped
        # without signaling completion (preserves its full context).
        sid = self.agent.read_session_id(sprint_dir)
        if sid:
            progress.agent_session_id = sid

        # 3a) declared a detached job -> the final message is premature (real work
        # still running detached): ignore it and sleep on the job instead.
        declared, state, job_refusal = self._declare_job(sprint, progress, sprint_dir)
        if declared:
            if state == "gone":
                # The declared job was not running on its host: tell the agent now,
                # with whatever it wrote, rather than sleeping on nothing. Its own
                # reason value (not "finished") so claude_executor can explain it
                # plainly — "finished" would wrongly imply the job ran to completion.
                self._collect_job(progress, sprint_dir)
                progress.assess_reason = "not_running"
                progress.job_token = ""
                progress.job_host = ""
                progress.job_collect = []
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached job declared ({progress.job_note})")
            return BeatOutcome.PROGRESSED                          # stay executing, sleep on the job

        # 3b) signaled completion via finished.json -> the ONLY accepted "done".
        finished = self._read_finished_json(sprint_dir)
        if finished is not None:
            progress.ambiguous_exits = 0
            artifacts.release_for_sprint(self.substrate, sprint, time.time())  # snapshot deliverables
            result = Result(
                id=f"{sprint.id}-result", sprint=sprint.id,
                summary=finished["summary"] or text or "(agent produced no output)",
                completed_at=time.time(),
            )
            self.substrate.save_result(result)
            set_status(sprint, SprintStatus.DONE)
            sprint.results = [result.id]
            self.substrate.save_sprint(sprint)
            if sprint.program and finished["host_notes"]:
                # What this sprint learned about the machine it ran on. It waits as a
                # report until the PM folds it into the program's note for that server.
                # Filed AFTER the sprint is done and best-effort: a note is an extra,
                # and a full disk or a host name the notes layer refuses must never
                # turn a finished sprint into a failing beat (O9).
                try:
                    self.substrate.add_host_report(
                        sprint.program, sprint_id=sprint.id,
                        host=self._slots.host(sprint.id)["name"] or LOCAL,
                        text=finished["host_notes"], source="finished", now=time.time())
                except (OSError, ValueError):
                    pass
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
            artifacts.release_for_sprint(self.substrate, sprint, time.time())
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
                                  self._nudge(sprint_dir, job_refusal), sprint.model,
                                  self._sprint_cwd(sprint))
        progress.agent_token = token
        # A resume is its own Claude call and its own row: it spends a window
        # exactly like a fresh launch does.
        progress.agent_call = usage_meter.start_call(
            self.substrate.repo_root, "worker", program=sprint.program or "",
            sprint=sprint.id, model=sprint.model,
            limits=usage_meter.current_window(), token=str(token))
        progress.started_at = time.time()
        self.substrate.save_progress(progress)
        self.substrate.commit(
            f"sprint {sprint.id}: no done-signal — resuming to ask "
            f"(attempt {progress.ambiguous_exits})")
        return BeatOutcome.PROGRESSED

    # The fields a job-collect pass (or _reap_job) may change — reloaded onto a
    # fresh copy of progress before saving, in run_escalated_beat, so a slow rsync
    # can never overwrite a concurrent PM/human answer applied while it ran (Fix C).
    _JOB_COLLECT_FIELDS = ("job_token", "job_host", "job_collect", "collect_note",
                           "assess_reason", "job_out", "job_note", "job_next_wake",
                           "job_max_seconds", "job_started_at")

    def _save_job_fields(self, sprint_id: str, stale_progress) -> None:
        """Reload progress fresh and copy onto it only the job/collect fields
        `stale_progress` carries — never the whole (possibly stale) object, so a
        concurrent answer's writes (resume_note, reallocate_to, pm_answered,
        escalation, stop_requested, and the sprint's own status) survive."""
        fresh = self.substrate.load_progress(sprint_id)
        for f in self._JOB_COLLECT_FIELDS:
            setattr(fresh, f, getattr(stale_progress, f))
        self.substrate.save_progress(fresh)

    def run_escalated_beat(self, sprint: Sprint) -> BeatOutcome:
        """The sprint is held pending an answer: never launch the agent. Still watch
        and collect a tracked job so it isn't orphaned while nobody is looking, and
        still honor a human's decision to give up on it."""
        progress = self.substrate.load_progress(sprint.id)
        sprint_dir = self.substrate.sprint_dir(sprint.id)

        if progress.stop_requested:
            what = str((progress.escalation or {}).get("what", ""))
            last_error = "stopped by a human after an escalation: " + what
            try:
                self.stop_sprint(sprint, collect=True)
            except Exception as exc:
                last_error += f" (stopping it also failed: {exc})"
            sprint = self.substrate.load_sprint(sprint.id)        # reload: stop_sprint may
            progress = self.substrate.load_progress(sprint.id)    # have taken a while
            if sprint.status != SprintStatus.ESCALATED:
                # Someone else already moved this sprint on (e.g. a second dispatcher
                # instance beat us to it) while stop_sprint ran — the stop we just did
                # is harmless (idempotent once nothing is left running), but writing
                # FAILED over whatever it is now would clobber real state.
                return BeatOutcome.PROGRESSED
            if progress.collect_note:
                last_error += f"; {progress.collect_note}"
            progress.last_error = last_error
            progress.escalation = {}
            progress.stop_requested = False
            set_status(sprint, SprintStatus.FAILED)
            artifacts.release_for_sprint(self.substrate, sprint, time.time())
            self.substrate.save_sprint(sprint)
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: FAILED after a human stop following an escalation")
            return BeatOutcome.COMPLETED

        if progress.job_token and not self._job_alive(progress.job_token):
            progress.assess_reason = "lost" if self._last_job_state == "lost" else "finished"
            self._collect_job(progress, sprint_dir)          # may rsync for minutes
            progress.job_token = ""
            progress.job_host = ""
            progress.job_collect = []
            self._save_job_fields(sprint.id, progress)
            self.substrate.commit(f"sprint {sprint.id}: job ended while escalated")

        self._slots.release(sprint.id)
        return BeatOutcome.PROGRESSED

    def stop_sprint(self, sprint: Sprint, *, collect: bool = False) -> list[str]:
        """Stop the sprint's running agent and/or its tracked detached job, and
        clear whichever was set so a later beat relaunches (the agent resumes
        from its scratchpad). Returns [sprint.id] if either was stopped, else [].

        `collect` copies the job's declared outputs back before the job fields are
        cleared, and is the caller's to ask for: a stop that ends the sprint (a human
        pressing Stop) keeps what the job produced, while a stop that only pauses the
        work — the dispatcher reconciling a leaseless sprint — leaves the outputs where
        they are for the relaunch to find. May rsync for minutes when asked."""
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
                self._last_terminate_ok = False
            if collect:
                # After the kill, so nothing is still being written mid-copy; the
                # could-not-stop line below is appended after, since a collect writes
                # `collect_note` outright.
                self._collect_job(progress, self.substrate.sprint_dir(sprint.id),
                                  stopping=True)
            if not self._last_terminate_ok:
                line = _could_not_stop_line(progress.job_host)
                progress.collect_note = (f"{progress.collect_note}\n{line}"
                                         if progress.collect_note else line)
            progress.job_token = ""
            progress.job_host = ""
            progress.job_collect = []
            stopped = True
        if not stopped:
            return []
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: agent stopped")
        return [sprint.id]

    def relocate(self, sprint: Sprint) -> None:
        """Carry out a PM/human's `reallocate` answer: stop and collect any job still
        tracked on the old host, then move the sprint's held host to
        `progress.reallocate_to`. Called by the dispatcher before it would otherwise
        beat this (still EXECUTING) sprint — no agent is running at this point, but a
        still-tracked detached job on the old host must not be left running there."""
        progress = self.substrate.load_progress(sprint.id)
        sprint_dir = self.substrate.sprint_dir(sprint.id)
        old, new = progress.host, progress.reallocate_to
        if progress.job_token:
            try:
                self._terminate(progress.job_token)
            except Exception:
                self._last_terminate_ok = False
            self._collect_job(progress, sprint_dir)
            if not self._last_terminate_ok:
                line = _could_not_stop_line(progress.job_host)
                progress.collect_note = (f"{progress.collect_note}\n{line}"
                                         if progress.collect_note else line)
        progress.job_token = ""
        progress.job_host = ""
        progress.job_collect = []
        progress.job_out = progress.job_note = progress.assess_reason = ""
        progress.job_next_wake = progress.job_max_seconds = 0.0
        progress.job_started_at = None
        progress.agent_token = ""
        progress.agent_session_id = ""
        progress.host = new
        progress.reallocate_to = ""
        progress.gpu_devices = []
        note = (f"You were moved from {old or 'this machine'} to {new}. You start fresh "
                "there with only what is in the sprint folder (including collected/).")
        progress.resume_note = f"{note}\n\n{progress.resume_note}" if progress.resume_note else note
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: reallocated {old} → {new}")

    def is_yieldable(self, sprint_id: str) -> bool:
        """True if the sprint can safely yield its lease RIGHT NOW: nothing in
        flight and nothing uncollected. A non-empty `agent_token` means an agent
        run was launched and NOT yet collected — it may still be running, or it may
        have just exited leaving output (a fresh finished.json / job.json) that the
        next beat must collect. Hibernating in that window would discard a completed
        result or orphan a just-declared detached job, so it is NOT yieldable. Only
        a fully-idle sprint (token cleared) with no live job may yield. An ESCALATED
        sprint is never yieldable either, whatever its agent/job state: it is held on
        purpose pending an answer, not a hibernation candidate to free capacity for
        someone else — hibernating and re-granting it would relaunch the agent behind
        the escalation's back."""
        if self.substrate.load_sprint(sprint_id).status == SprintStatus.ESCALATED:
            return False
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
            progress.job_host = ""
            progress.job_collect = []
            progress.job_started_at = None
            progress.job_next_wake = 0.0
            progress.job_max_seconds = 0.0
            progress.job_expected_seconds = 0.0
        set_status(sprint, SprintStatus.HIBERNATED, by="dispatcher", action="hibernate")
        self.substrate.save_sprint(sprint)
        self.substrate.save_progress(progress)
        self.substrate.commit(f"sprint {sprint.id}: hibernated (yield for higher-priority work)")
