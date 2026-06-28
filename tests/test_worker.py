from tests.conftest import FakeAgent

from coscience.models import BeatOutcome, Program, Result, Sprint, SprintStatus
from coscience.worker import Worker


def _approved(sid, plan=("do the thing",), program=None, title="", summary=""):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=list(plan),
                  program=program, title=title, summary=summary)


def test_idle_when_no_work(substrate, agent):
    assert Worker(substrate, agent).run_one_beat() == BeatOutcome.IDLE


def test_first_beat_claims_and_launches_one_agent(substrate, agent):
    substrate.save_sprint(_approved("sp1"))
    outcome = Worker(substrate, agent).run_one_beat()
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert agent.started == ["sp1"]                       # launched exactly once
    assert substrate.load_progress("sp1").agent_token     # token recorded


def test_running_agent_is_left_alone(substrate):
    agent = FakeAgent(linger=5)                            # stays "running"
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    worker.run_one_beat()                                 # launch
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # still running, not relaunched
    assert agent.started == ["sp1"]                       # NOT launched again


def test_finished_agent_is_collected_into_a_result(substrate):
    agent = FakeAgent(result="min gap is 2, witness 1000037/1000039", status="ok")
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    worker.run_one_beat()                                 # launch
    assert worker.run_one_beat() == BeatOutcome.COMPLETED  # collect
    sprint = substrate.load_sprint("sp1")
    assert sprint.status == SprintStatus.DONE
    assert sprint.results == ["sp1-result"]
    assert "min gap is 2" in substrate.load_result("sp1-result").summary
    assert substrate.load_progress("sp1").agent_token == ""  # cleared


def test_interrupted_agent_relaunches_to_resume(substrate):
    # The agent died mid-run (no exit sentinel): the worker must relaunch, not finish.
    agent = FakeAgent(status="interrupted")
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    worker.run_one_beat()                                 # launch #1
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # ended -> interrupted -> clear
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING  # not done
    worker.run_one_beat()                                 # launch #2 (resume)
    assert agent.started == ["sp1", "sp1"]


def test_failed_run_completes_with_the_failure_as_result(substrate):
    # exit != 0 means it ran to completion but failed: surface it, don't loop forever.
    agent = FakeAgent(result="ImportError: no module named sympy", status="failed")
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    worker.run_one_beat()
    assert worker.run_one_beat() == BeatOutcome.COMPLETED
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    assert "ImportError" in substrate.load_result("sp1-result").summary


def test_agent_is_handed_program_and_prior_results(substrate):
    substrate.save_program(Program(id="p1", title="Demo", goals="find the prime gap"))
    done = Sprint(id="p1-c0-base", status=SprintStatus.DONE, goals="g", plan=[],
                  program="p1", title="Baseline", results=["r0"])
    substrate.save_sprint(done)
    substrate.save_result(Result(id="r0", sprint="p1-c0-base", summary="min_gap = 2"))
    cur = _approved("p1-c1-next", program="p1", title="Cross-check", summary="verify it")
    substrate.save_sprint(cur)

    class Capturing(FakeAgent):
        def start(self, sprint, context, sprint_dir, repo_root=None):
            self.ctx = context
            return super().start(sprint, context, sprint_dir, repo_root)

    agent = Capturing()
    Worker(substrate, agent).run_sprint_beat(substrate.load_sprint("p1-c1-next"))
    assert agent.ctx.program_goal == "find the prime gap"
    assert agent.ctx.sprint_title == "Cross-check"
    assert any("min_gap = 2" in p for p in agent.ctx.prior_results)
    assert agent.ctx.repo_root == substrate.repo_root
