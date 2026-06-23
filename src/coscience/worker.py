"""The Worker: one bounded unit of work per heartbeat."""
from __future__ import annotations

from coscience.executor import StepExecutor
from coscience.models import BeatOutcome, Result, SprintStatus
from coscience.substrate import Substrate


class Worker:
    def __init__(self, substrate: Substrate, executor: StepExecutor):
        self.substrate = substrate
        self.executor = executor

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

        progress = self.substrate.load_progress(sprint.id)
        next_step = next(
            (s for s in sprint.plan if s.id not in progress.completed_steps), None
        )

        if next_step is None:
            result = Result(
                id=f"{sprint.id}-result",
                sprint=sprint.id,
                summary=f"Sprint {sprint.id} completed {len(sprint.plan)} steps.",
            )
            self.substrate.save_result(result)
            sprint.status = SprintStatus.DONE
            sprint.results = [result.id]
            self.substrate.save_sprint(sprint)
            self.substrate.commit(f"sprint {sprint.id}: done, result {result.id}")
            return BeatOutcome.COMPLETED

        step_result = self.executor.run(next_step)
        if step_result.completed:
            progress.completed_steps.append(next_step.id)
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} done")
        return BeatOutcome.PROGRESSED
