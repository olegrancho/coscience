from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.worker import Worker


def test_leaseless_running_agent_is_reconciled_killed(substrate):
    # Orphan state (as after a dispatcher outage that expired the lease): an
    # EXECUTING sprint with a running agent but NO lease in the ledger.
    agent = FakeAgent(linger=10**6)
    orph = Sprint(id="ORPH", status=SprintStatus.EXECUTING, goals="g", plan=["work"],
                  resources_required={"gpu": 1.0}, priority=0)
    substrate.save_sprint(orph)
    Worker(substrate, agent).run_sprint_beat(orph)        # launch agent, no lease
    token = substrate.load_progress("ORPH").agent_token
    assert agent.is_running(token)

    # A higher-priority sprint claims the single GPU, so ORPH cannot be re-adopted.
    substrate.save_sprint(Sprint(id="HOG", status=SprintStatus.QUEUED, goals="g",
                                 plan=["go"], resources_required={"gpu": 1.0}, priority=9))
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    report = disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("HOG") is not None
    assert disp.ledger.lease_for("ORPH") is None
    assert report.reconciled == 1
    assert token in agent.stopped                         # orphan agent killed
    assert substrate.load_progress("ORPH").agent_token == ""
