from tests.conftest import FakeAgent

from coscience.models import Sprint, SprintStatus
from coscience.worker import Worker


def _running_sprint(sid):
    return Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["do it"])


def test_stop_sprint_kills_the_agent_and_clears_it(substrate):
    agent = FakeAgent(linger=5)
    substrate.save_sprint(_running_sprint("sp1"))
    worker = Worker(substrate, agent)
    worker.run_sprint_beat(substrate.load_sprint("sp1"))   # launch the agent
    token = substrate.load_progress("sp1").agent_token
    assert token and worker.agent_running("sp1")

    assert worker.stop_sprint(substrate.load_sprint("sp1")) == ["sp1"]
    assert token in agent.stopped
    assert substrate.load_progress("sp1").agent_token == ""  # cleared -> will relaunch
    assert not worker.agent_running("sp1")


def test_stop_sprint_noop_when_no_agent(substrate, agent):
    substrate.save_sprint(_running_sprint("sp2"))
    assert Worker(substrate, agent).stop_sprint(substrate.load_sprint("sp2")) == []
