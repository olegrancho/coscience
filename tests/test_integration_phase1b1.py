import time

from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def test_preempted_sprint_is_killed_then_resumes_and_both_complete(substrate):
    # V: low priority, a long-running agent. H: high priority. linger keeps the
    # agents 'running' long enough for the preemption to actually happen.
    agent = FakeAgent(linger=3)
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.QUEUED, goals="g", plan=["long work"],
        resources_required={"gpu": 1.0}, priority=0))
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))

    disp.run_one_cycle(now=0.0)            # V launches its agent
    assert substrate.load_progress("V").agent_token

    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.QUEUED, goals="g", plan=["go"],
        resources_required={"gpu": 1.0}, priority=9))

    h_done_first = False
    t = 1
    deadline = time.time() + 30
    while not (substrate.load_sprint("V").status == SprintStatus.DONE
               and substrate.load_sprint("H").status == SprintStatus.DONE):
        assert time.time() < deadline, "sprints did not both complete"
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0      # never overcommit
        if not h_done_first and substrate.load_sprint("H").status == SprintStatus.DONE:
            h_done_first = substrate.load_sprint("V").status != SprintStatus.DONE
        t += 1
        time.sleep(0.02)

    assert h_done_first                    # H preempted V and finished first
    assert substrate.load_sprint("V").status == SprintStatus.DONE   # V resumed + finished
    assert disp.ledger.all_leases() == []
