from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def test_preemption_kills_the_victims_agent(substrate):
    # V (low priority, preemptible) holds the GPU with a running agent.
    agent = FakeAgent(linger=10**6)
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.APPROVED, goals="g", plan=["long work"],
        resources_required={"gpu": 1.0}, priority=0))
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=0.0)            # V granted + agent launched
    disp.ledger.load()
    token = substrate.load_progress("V").agent_token
    assert disp.ledger.lease_for("V") is not None
    assert agent.is_running(token)

    # H (high priority) arrives and needs the same GPU.
    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.APPROVED, goals="g", plan=["go"],
        resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)            # H preempts V

    disp.ledger.load()
    assert disp.ledger.lease_for("H") is not None
    assert disp.ledger.lease_for("V") is None
    assert token in agent.stopped                          # V's agent terminated
    assert substrate.load_progress("V").agent_token == ""  # armed for relaunch
