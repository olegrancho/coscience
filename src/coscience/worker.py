"""The Worker: one bounded unit of work per heartbeat."""
from __future__ import annotations

from coscience.executor import StepExecutor, is_running, launch_detached, terminate_detached
from coscience.models import BeatOutcome, Result, Sprint, SprintStatus
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
        return self.run_sprint_beat(sprint)

    def run_sprint_beat(self, sprint: Sprint) -> BeatOutcome:
        progress = self.substrate.load_progress(sprint.id)
        next_step = next(
            (s for s in sprint.plan if s.id not in progress.completed_steps), None
        )

        if next_step is None:
            lines = [f"Sprint {sprint.id} completed {len(sprint.plan)} steps.", ""]
            for step in sprint.plan:
                out = progress.outputs.get(step.id, "").strip()
                if out:
                    lines.append(f"## {step.id}\n\n{out}\n")
            result = Result(
                id=f"{sprint.id}-result",
                sprint=sprint.id,
                summary="\n".join(lines).strip(),
            )
            self.substrate.save_result(result)
            sprint.status = SprintStatus.DONE
            sprint.results = [result.id]
            self.substrate.save_sprint(sprint)
            self.substrate.commit(f"sprint {sprint.id}: done, result {result.id}")
            return BeatOutcome.COMPLETED

        if next_step.run.startswith("detached:"):
            command = next_step.run[len("detached:"):].strip()
            token = progress.detached.get(next_step.id)
            if token is None:
                progress.detached[next_step.id] = launch_detached(command)
                self.substrate.save_progress(progress)
                self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} launched")
                return BeatOutcome.PROGRESSED
            if is_running(token):
                return BeatOutcome.PROGRESSED
            progress.completed_steps.append(next_step.id)
            del progress.detached[next_step.id]
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached step {next_step.id} done")
            return BeatOutcome.PROGRESSED

        step_result = self.executor.run(next_step)
        if step_result.completed:
            progress.completed_steps.append(next_step.id)
            progress.outputs[next_step.id] = (step_result.output or "")[:2000]
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} done")
        return BeatOutcome.PROGRESSED

    def stop_sprint(self, sprint: Sprint) -> list[str]:
        """Terminate the sprint's running detached jobs and clear them so the
        steps relaunch on a later beat. Returns the stopped step ids."""
        progress = self.substrate.load_progress(sprint.id)
        stopped = list(progress.detached.keys())
        for _step_id, token in list(progress.detached.items()):
            terminate_detached(token)
        if stopped:
            progress.detached = {}
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: stopped detached jobs {stopped}")
        return stopped
