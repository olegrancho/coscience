import time

from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.worker import Worker


def _detached_sprint(sid):
    return Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g",
                  plan=[Step("job", "detached: sleep 30")])


def test_stop_sprint_kills_and_clears(substrate):
    s = _detached_sprint("sp1")
    substrate.save_sprint(s)
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(s)  # launches the detached job, records its pid
    pid = substrate.load_progress("sp1").detached["job"]
    assert is_running(pid) is True

    stopped = worker.stop_sprint(substrate.load_sprint("sp1"))
    assert stopped == ["job"]

    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False
    progress = substrate.load_progress("sp1")
    assert progress.detached == {}
    assert "job" not in progress.completed_steps  # not completed -> will relaunch


def test_stop_sprint_noop_when_no_detached(substrate):
    s = Sprint(id="sp2", status=SprintStatus.EXECUTING, goals="g",
               plan=[Step("s1", "echo hi")])
    substrate.save_sprint(s)
    assert Worker(substrate, ShellStepExecutor()).stop_sprint(s) == []
