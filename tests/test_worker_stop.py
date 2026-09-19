from tests.conftest import FakeAgent

from coscience.models import BeatOutcome, Sprint, SprintStatus
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


# --- O18: a human's stop request, honored by run_sprint_beat ------------------------

def test_run_sprint_beat_honors_a_stop_request_on_a_running_agent(substrate):
    agent = FakeAgent(linger=5)
    substrate.save_sprint(_running_sprint("sp1"))
    worker = Worker(substrate, agent)
    worker.run_sprint_beat(substrate.load_sprint("sp1"))   # launch the agent
    token = substrate.load_progress("sp1").agent_token
    assert token and worker.agent_running("sp1")

    prog = substrate.load_progress("sp1")
    prog.stop_requested = True
    substrate.save_progress(prog)

    outcome = worker.run_sprint_beat(substrate.load_sprint("sp1"))
    assert outcome == BeatOutcome.COMPLETED
    sp = substrate.load_sprint("sp1")
    prog = substrate.load_progress("sp1")
    assert sp.status == SprintStatus.CANCELED               # a human stop is a cancel, not a failure
    assert "stopped by a human" in prog.last_error
    assert token in agent.stopped
    assert prog.agent_token == ""
    assert prog.stop_requested is False
    assert prog.escalation == {}


def test_run_sprint_beat_honors_a_stop_request_while_asleep_on_a_job(substrate):
    # A sprint asleep on a detached job (no agent running it) must be stopped too —
    # the stop check runs before the sleeping-on-a-job branch.
    substrate.save_sprint(_running_sprint("sp1"))
    prog = substrate.load_progress("sp1")
    prog.job_token = "1:1"
    prog.stop_requested = True
    substrate.save_progress(prog)

    killed = []
    agent = FakeAgent()
    worker = Worker(substrate, agent, job_alive=lambda t: True,
                    terminate=lambda t: killed.append(t))
    outcome = worker.run_sprint_beat(substrate.load_sprint("sp1"))
    assert outcome == BeatOutcome.COMPLETED
    assert killed == ["1:1"]
    sp = substrate.load_sprint("sp1")
    prog = substrate.load_progress("sp1")
    assert sp.status == SprintStatus.CANCELED
    assert prog.job_token == ""             # cleared, never left orphaned
    assert agent.started == []              # never relaunched: caught before branch A


def test_run_sprint_beat_stop_skips_canceling_if_the_sprint_moved_on(substrate, monkeypatch):
    substrate.save_sprint(_running_sprint("sp1"))
    prog = substrate.load_progress("sp1")
    prog.stop_requested = True
    substrate.save_progress(prog)

    worker = Worker(substrate, FakeAgent())

    def fake_stop(sprint):
        # A second dispatcher instance already moved this sprint on while
        # stop_sprint (this call) ran.
        moved = substrate.load_sprint("sp1")
        moved.status = SprintStatus.ESCALATED
        substrate.save_sprint(moved)
        return []
    monkeypatch.setattr(worker, "stop_sprint", fake_stop)

    outcome = worker.run_sprint_beat(substrate.load_sprint("sp1"))
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.ESCALATED   # not clobbered


def test_run_sprint_beat_stop_leaves_a_finished_sprint_alone(substrate, monkeypatch):
    # O18 fix round 1: the guard now accepts EXECUTING or HIBERNATED after the
    # stop, but must still reject anything else — including DONE, reached here
    # while stop_sprint (mid-call) ran.
    substrate.save_sprint(_running_sprint("sp1"))
    prog = substrate.load_progress("sp1")
    prog.stop_requested = True
    substrate.save_progress(prog)

    worker = Worker(substrate, FakeAgent())

    def fake_stop(sprint):
        moved = substrate.load_sprint("sp1")
        moved.status = SprintStatus.DONE
        substrate.save_sprint(moved)
        return []
    monkeypatch.setattr(worker, "stop_sprint", fake_stop)

    outcome = worker.run_sprint_beat(substrate.load_sprint("sp1"))
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE   # not clobbered
