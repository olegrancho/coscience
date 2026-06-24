import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _disp(substrate):
    return Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))


def test_preemption_kills_victim_running_job(substrate):
    # V (low priority, preemptible) holds the GPU with a long detached job.
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("job", "detached: sleep 30")],
        resources_required={"gpu": 1.0}, priority=0))
    disp = _disp(substrate)
    disp.run_one_cycle(now=0.0)  # V granted + launches its job
    disp.ledger.load()
    pid = substrate.load_progress("V").detached["job"]
    assert disp.ledger.lease_for("V") is not None
    assert is_running(pid) is True

    # H (high priority) arrives and needs the same GPU.
    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true")],
        resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)  # H preempts V

    disp.ledger.load()
    assert disp.ledger.lease_for("H") is not None
    assert disp.ledger.lease_for("V") is None

    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False  # V's job was physically terminated
    assert substrate.load_progress("V").detached == {}  # armed for relaunch
