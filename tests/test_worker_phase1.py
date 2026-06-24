from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.worker import Worker


def _sprint(sid, steps, status=SprintStatus.EXECUTING):
    return Sprint(id=sid, status=status, goals="g", plan=steps)


def test_run_sprint_beat_runs_one_step_of_given_sprint(substrate):
    s = _sprint("sp1", [Step("s1", "echo hi"), Step("s2", "echo bye")])
    substrate.save_sprint(s)
    outcome = Worker(substrate, ShellStepExecutor()).run_sprint_beat(s)
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_progress("sp1").completed_steps == ["s1"]


def test_run_sprint_beat_captures_output(substrate):
    s = _sprint("sp1", [Step("s1", "echo captured-out")])
    substrate.save_sprint(s)
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(s)  # runs s1
    assert "captured-out" in substrate.load_progress("sp1").outputs["s1"]


def test_completion_writes_outputs_into_result(substrate):
    s = _sprint("sp1", [Step("s1", "echo hello-world")])
    substrate.save_sprint(s)
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(s)  # s1 done
    assert worker.run_sprint_beat(substrate.load_sprint("sp1")) == BeatOutcome.COMPLETED
    result_text = (substrate.repo_root / "results" / "sp1-result.md").read_text()
    assert "hello-world" in result_text


def test_run_one_beat_still_claims_and_runs(substrate):
    # Phase 0 behavior preserved.
    substrate.save_sprint(_sprint("sp1", [Step("s1", "true")], status=SprintStatus.APPROVED))
    assert Worker(substrate, ShellStepExecutor()).run_one_beat() == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
