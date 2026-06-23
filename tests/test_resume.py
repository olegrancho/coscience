from coscience.executor import ShellStepExecutor
from coscience.models import ProgressState, Sprint, SprintStatus, Step
from coscience.worker import Worker


def _sprint_appending_to(marker, n_steps):
    return Sprint(
        id="sp1",
        status=SprintStatus.APPROVED,
        goals="g",
        plan=[Step(f"s{i}", f"echo s{i} >> {marker}") for i in range(n_steps)],
    )


def test_fresh_worker_resumes_without_redoing_steps(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_sprint_appending_to(marker, 4))

    # First "process": run two beats, then it "dies" (we drop the object).
    Worker(substrate, ShellStepExecutor()).run_one_beat()
    Worker(substrate, ShellStepExecutor()).run_one_beat()
    assert substrate.load_progress("sp1").completed_steps == ["s0", "s1"]

    # Brand-new Worker objects (simulating restarts) finish the job.
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_one_beat()  # s2
    worker.run_one_beat()  # s3
    worker.run_one_beat()  # complete

    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    # Each step ran EXACTLY once across all restarts — no duplication.
    lines = marker.read_text().split()
    assert lines == ["s0", "s1", "s2", "s3"]


def test_resume_after_already_completed_step_is_recorded(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_sprint_appending_to(marker, 2))
    # Pretend s0 already ran in a previous life: seed progress directly.
    substrate.save_progress(ProgressState(sprint_id="sp1", completed_steps=["s0"]))
    substrate.save_sprint(
        Sprint(id="sp1", status=SprintStatus.EXECUTING, goals="g",
               plan=_sprint_appending_to(marker, 2).plan)
    )

    Worker(substrate, ShellStepExecutor()).run_one_beat()  # must run s1, NOT s0
    assert marker.read_text().split() == ["s1"]
