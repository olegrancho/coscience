import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.worker import Worker


def test_leaseless_running_job_is_reconciled_killed(substrate):
    # Orphan state (as produced by a dispatcher outage that expired the lease):
    # an EXECUTING sprint with a running detached job but NO lease in the ledger.
    orph = Sprint(id="ORPH", status=SprintStatus.EXECUTING, goals="g",
                  plan=[Step("job", "detached: sleep 30")],
                  resources_required={"gpu": 1.0}, priority=0)
    substrate.save_sprint(orph)
    Worker(substrate, ShellStepExecutor()).run_sprint_beat(orph)  # launches the job; no lease
    pid = substrate.load_progress("ORPH").detached["job"]
    assert is_running(pid) is True

    # A higher-priority sprint claims the single GPU, so ORPH cannot be re-adopted.
    substrate.save_sprint(Sprint(id="HOG", status=SprintStatus.APPROVED, goals="g",
                                 plan=[Step("s1", "true")],
                                 resources_required={"gpu": 1.0}, priority=9))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))
    report = disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("HOG") is not None
    assert disp.ledger.lease_for("ORPH") is None
    assert report.reconciled == 1

    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False  # orphaned job reconciled (killed)
    assert substrate.load_progress("ORPH").detached == {}
