import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.worker import Worker


def _orphan_with_running_job(substrate, sid, run="detached: sleep 30"):
    s = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g",
               plan=[Step("job", run)], resources_required={"gpu": 1.0}, priority=0)
    substrate.save_sprint(s)
    Worker(substrate, ShellStepExecutor()).run_sprint_beat(s)  # launch job, no lease
    return substrate.load_progress(sid).detached["job"]


def test_orphan_is_readopted_when_capacity_free(substrate):
    pid = _orphan_with_running_job(substrate, "ORPH")
    assert is_running(pid) is True
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))
    report = disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("ORPH") is not None  # re-adopted, not killed
    assert report.reconciled == 0
    assert is_running(pid) is True                     # the same job keeps running
    assert substrate.load_progress("ORPH").detached["job"] == pid


def test_readopted_orphan_runs_to_completion(substrate):
    pid = _orphan_with_running_job(substrate, "ORPH", run="detached: sleep 1")
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))
    t = 0
    deadline = time.time() + 20
    while substrate.load_sprint("ORPH").status != SprintStatus.DONE:
        assert time.time() < deadline, "re-adopted orphan never completed"
        disp.run_one_cycle(now=float(t))
        t += 1
        time.sleep(0.1)
    assert "job" in substrate.load_progress("ORPH").completed_steps
    disp.ledger.load()
    assert disp.ledger.all_leases() == []
