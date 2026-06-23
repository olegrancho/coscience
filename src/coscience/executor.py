"""Step executors: how a sprint step actually gets run."""
from __future__ import annotations

import os
import subprocess
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


def launch_detached(command: str) -> int:
    """Start a shell command fully detached from this process; return its PID."""
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # survives parent death
    )
    return proc.pid


def is_running(pid: int) -> bool:
    """True if a process with this PID is alive (zombies are treated as dead)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal
    # On Linux, os.kill(pid, 0) succeeds for zombie processes; exclude them.
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("State:"):
                    return line.split()[1] != "Z"
    except OSError:
        pass
    return True
