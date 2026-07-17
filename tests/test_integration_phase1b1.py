import time

from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def test_higher_priority_waits_for_running_sprint_then_both_complete(substrate):
    # Cooperative model: a running agent is NEVER killed. H (high priority) must
    # wait for V (already running) to reach completion, then runs. Both finish; V's
    # agent is never stopped; capacity is never overcommitted.
    agent = FakeAgent(linger=3)            # each agent 'runs' a few polls then finishes
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.QUEUED, goals="g", plan=["long work"],
        resources_required={"gpu": 1.0}, priority=0))
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))

    disp.run_one_cycle(now=0.0)            # V launches its agent
    vtoken = substrate.load_progress("V").agent_token
    assert vtoken

    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.QUEUED, goals="g", plan=["go"],
        resources_required={"gpu": 1.0}, priority=9))

    t = 1
    deadline = time.time() + 30
    while not (substrate.load_sprint("V").status == SprintStatus.DONE
               and substrate.load_sprint("H").status == SprintStatus.DONE):
        assert time.time() < deadline, "sprints did not both complete"
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0      # never overcommit
        t += 1
        time.sleep(0.02)

    assert vtoken not in agent.stopped     # V was never hard-killed for preemption
    assert substrate.load_sprint("V").status == SprintStatus.DONE
    assert substrate.load_sprint("H").status == SprintStatus.DONE
    assert disp.ledger.all_leases() == []
