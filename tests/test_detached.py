import time

from coscience.executor import ShellStepExecutor, is_running, launch_detached
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.worker import Worker


def test_launch_detached_and_is_running(tmp_path):
    pid = launch_detached(f"sleep 0.5; echo done > {tmp_path/'d.txt'}")
    assert is_running(pid) is True
    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False
    assert (tmp_path / "d.txt").read_text().strip() == "done"


def test_worker_waits_for_detached_then_completes(substrate, tmp_path):
    out = tmp_path / "out.txt"
    substrate.save_sprint(Sprint(
        id="sp1", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", f"detached: sleep 0.4; echo finished > {out}")],
    ))
    worker = Worker(substrate, ShellStepExecutor())

    # Beat 1: launches the job, records PID, NOT complete.
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED
    prog = substrate.load_progress("sp1")
    assert "s1" in prog.detached
    assert prog.completed_steps == []

    # Re-attach across a simulated restart: keep beating with FRESH workers
    # until the job finishes and the step is marked complete.
    deadline = time.time() + 10
    while substrate.load_sprint("sp1").status != SprintStatus.DONE:
        assert time.time() < deadline, "detached job never completed"
        Worker(substrate, ShellStepExecutor()).run_one_beat()
        time.sleep(0.1)

    assert out.read_text().strip() == "finished"
    assert substrate.load_progress("sp1").completed_steps == ["s1"]
    assert substrate.load_progress("sp1").detached == {}
