"""Step executors: how a sprint step actually gets run."""
from __future__ import annotations

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
