"""Step executors: how a sprint step actually gets run."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Protocol

from coscience.models import Step, StepResult


class StepExecutor(Protocol):
    def run(self, step: Step) -> StepResult:
        ...


class ShellStepExecutor:
    """Deterministic executor: runs the step's shell command."""

    def run(self, step: Step) -> StepResult:
        proc = subprocess.run(
            step.run, shell=True, capture_output=True, text=True
        )
        return StepResult(
            step_id=step.id,
            completed=proc.returncode == 0,
            output=(proc.stdout or "") + (proc.stderr or ""),
        )


def launch_detached(command: str) -> str:
    """Start a shell command fully detached from this process; return its
    identity token '<pid>:<starttime>'."""
    proc = subprocess.Popen(
        command, shell=True,
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
