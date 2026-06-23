"""Executor that delegates a step to a headless Claude Code session."""
from __future__ import annotations

import subprocess

from coscience.models import Step, StepResult


class ClaudeCodeExecutor:
    def __init__(self, claude_bin: str = "claude"):
        self.claude_bin = claude_bin

    def build_command(self, step: Step) -> list[str]:
        return [self.claude_bin, "-p", step.run, "--output-format", "text"]

    def run(self, step: Step) -> StepResult:
        proc = subprocess.run(
            self.build_command(step), capture_output=True, text=True
        )
        return StepResult(
            step_id=step.id,
            completed=proc.returncode == 0,
            output=proc.stdout or "",
        )
