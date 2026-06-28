import time

from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.worker import Worker


def _orphan_with_running_agent(substrate, agent, sid):
    s = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["work"],
               resources_required={"gpu": 1.0}, priority=0)
    substrate.save_sprint(s)
    Worker(substrate, agent).run_sprint_beat(s)           # launch agent, no lease
    return substrate.load_progress(sid).agent_token


def test_orphan_is_readopted_when_capacity_free(substrate):
    agent = FakeAgent(linger=10**6)
    token = _orphan_with_running_agent(substrate, agent, "ORPH")
    assert agent.is_running(token)
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    report = disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("ORPH") is not None       # re-adopted, not killed
    assert report.reconciled == 0
    assert substrate.load_progress("ORPH").agent_token == token  # same agent, not relaunched
    assert token not in agent.stopped


def test_readopted_orphan_runs_to_completion(substrate):
    agent = FakeAgent()                                    # finishes promptly
    _orphan_with_running_agent(substrate, agent, "ORPH")
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    t = 0
    deadline = time.time() + 20
    while substrate.load_sprint("ORPH").status != SprintStatus.DONE:
        assert time.time() < deadline, "re-adopted orphan never completed"
        disp.run_one_cycle(now=float(t))
        t += 1
        time.sleep(0.02)
    disp.ledger.load()
    assert disp.ledger.all_leases() == []
