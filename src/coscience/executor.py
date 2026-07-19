"""Detached-process helpers + the context an agent needs to run a sprint.

A sprint is carried out by one long-lived Claude agent launched detached (see
claude_executor.ClaudeAgent); these helpers start it, check whether it is still
alive, and stop it. Process identity is a '<pid>:<starttime>' token so a reused
PID can't be mistaken for the original job."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecutionContext:
    """The 'why and what' an agent needs to run a sprint: the program goal, the
    sprint it belongs to, the suggested steps (guidance), and prior results in
    this program."""
    program_title: str = ""
    program_goal: str = ""
    sprint_title: str = ""
    sprint_summary: str = ""
    sprint_goals: str = ""
    plan: list[str] = field(default_factory=list)            # suggested steps (guidance)
    prior_results: list[str] = field(default_factory=list)   # formatted "## label\n<summary>"
    human_comments: list[str] = field(default_factory=list)  # human feedback on this sprint
    feedback_threads: list[dict] = field(default_factory=list)  # open worker threads
                                                               # [{thread_id, text}] the agent may reply to
    repo_root: Path | None = None
    assess_reason: str = ""  # "" normal run; else why the agent is resuming to check a
                              # detached job ("finished"/"timed out"/"wake")
    job_out: str = ""        # path to the detached job's captured output (set when assessing)
    job_note: str = ""       # the job's own short description (set when assessing)
    artifacts: list[dict] = field(default_factory=list)  # [{aid, kind, work_path}] deliverables to write into work/


def launch_detached(command: str, cwd: "str | Path | None" = None) -> str:
    """Start a shell command fully detached from this process; return its
    identity token '<pid>:<starttime>'."""
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process_token(proc.pid)


def _starttime(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat: process start time in clock ticks since
    boot. Stable for the life of a PID; differs if the PID is later reused.
    Returns None if the process is gone / unreadable."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError:
        return None
    # comm (field 2) is parenthesised and may contain spaces/')'; split after the last ')'.
    rparen = data.rfind(")")
    if rparen == -1:
        return None
    fields = data[rparen + 2:].split()
    # After comm: index 0 == state (field 3); start-time is field 22 == index 19.
    return fields[19] if len(fields) > 19 else None


def process_token(pid: int) -> str:
    """Identity token '<pid>:<starttime>' for a process. Falls back to '<pid>:'
    when the start time can't be read (degrades to PID-only liveness)."""
    st = _starttime(pid)
    return f"{pid}:{st if st is not None else ''}"


def _parse_target(pid_or_token: "int | str") -> "tuple[int, str | None]":
    if isinstance(pid_or_token, int):
        return pid_or_token, None  # legacy: no identity to verify
    pid_str, _, st = str(pid_or_token).partition(":")
    return int(pid_str), (st or None)


def _identity_ok(pid: int, expected_st: "str | None") -> bool:
    """True if there is no identity to check (liveness-only), or the live
    process's current start time matches the captured one."""
    if expected_st is None:
        return True
    return _starttime(pid) == expected_st


def is_running(pid_or_token: "int | str") -> bool:
    """True if the target is alive AND (if a token was given) its identity
    matches. Zombies and reused PIDs are treated as not running."""
    pid, expected_st = _parse_target(pid_or_token)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but not ours to signal — still verify identity (stat is readable).
        return _identity_ok(pid, expected_st)
    if not _identity_ok(pid, expected_st):
        return False  # PID reused by a different process
    # On Linux, os.kill(pid, 0) succeeds for zombie processes; exclude them.
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("State:"):
                    return line.split()[1] != "Z"
    except OSError:
        pass
    return True


def terminate_detached(pid_or_token: "int | str", grace: float = 2.0) -> None:
    """Stop a detached job's whole process group (SIGTERM, then SIGKILL).

    PID-reuse guard: if a token was given and its identity does not match the
    live process, do nothing (the original job is gone; the PID belongs to a
    stranger). A dead/unknown PID is a no-op. A bare int falls back to plain
    liveness, preserving the original behavior.
    """
    pid, expected_st = _parse_target(pid_or_token)
    if not _identity_ok(pid, expected_st):
        return  # PID reused — never signal a stranger
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if not is_running(pid_or_token):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
