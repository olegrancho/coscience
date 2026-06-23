from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.worker import Worker


def _approved_sprint(sid, steps):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=steps)


def test_idle_when_no_work(substrate):
    assert Worker(substrate, ShellStepExecutor()).run_one_beat() == BeatOutcome.IDLE


def test_first_beat_claims_approved_and_runs_one_step(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_approved_sprint("sp1", [
        Step("s1", f"echo one >> {marker}"),
        Step("s2", f"echo two >> {marker}"),
    ]))
    outcome = Worker(substrate, ShellStepExecutor()).run_one_beat()
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert substrate.load_progress("sp1").completed_steps == ["s1"]
    assert marker.read_text().count("one") == 1
    assert "two" not in marker.read_text()  # only ONE step per beat


def test_beats_complete_the_sprint_and_write_result(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_approved_sprint("sp1", [
        Step("s1", f"echo one >> {marker}"),
        Step("s2", f"echo two >> {marker}"),
    ]))
    worker = Worker(substrate, ShellStepExecutor())
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # s1
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # s2
    assert worker.run_one_beat() == BeatOutcome.COMPLETED   # result
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    result_text = (substrate.repo_root / "results" / "sp1-result.md").read_text()
    assert "sprint: sp1" in result_text
