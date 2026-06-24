import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def test_preempted_sprint_is_killed_then_resumes_and_both_complete(substrate):
    # V: low priority, a short detached job. H: high priority, quick.
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("job", "detached: sleep 1")],
        resources_required={"gpu": 1.0}, priority=0))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))

    disp.run_one_cycle(now=0.0)  # V launches its job
    assert "job" in substrate.load_progress("V").detached  # V's job is running

    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true")],
        resources_required={"gpu": 1.0}, priority=9))

    h_done_first = False
    t = 1
    deadline = time.time() + 30
    while not (substrate.load_sprint("V").status == SprintStatus.DONE
               and substrate.load_sprint("H").status == SprintStatus.DONE):
        assert time.time() < deadline, "sprints did not both complete"
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0  # never physically overcommit
        if not h_done_first and substrate.load_sprint("H").status == SprintStatus.DONE:
            h_done_first = substrate.load_sprint("V").status != SprintStatus.DONE
        t += 1
        time.sleep(0.1)

    assert h_done_first  # H preempted V and finished first
    # V relaunched after preemption with a fresh pid (the old one was cleared/killed)
    assert "job" in substrate.load_progress("V").completed_steps
    assert disp.ledger.all_leases() == []  # everything released at the end
