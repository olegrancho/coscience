import pytest

from tests.conftest import FakeAgent

from coscience.models import BeatOutcome, Program, Result, Sprint, SprintStatus
from coscience.worker import Worker


def _approved(sid, plan=("do the thing",), program=None, title="", summary=""):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=list(plan),
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


def test_failed_run_is_not_laundered_into_a_result(substrate):
    # exit != 0 (crash) must NOT become a "done" sprint with the crash text as result.
    agent = FakeAgent(result="ImportError: no module named sympy", status="failed")
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    worker.run_one_beat()                                  # launch
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # ended failed -> retry, not done
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert substrate.load_sprint("sp1").results == []
    with pytest.raises(Exception):
        substrate.load_result("sp1-result")               # no result written
    assert substrate.load_progress("sp1").agent_token == ""  # cleared -> relaunches


def test_only_worker_comments_reach_the_agent(substrate):
    s = _approved("sp1")
    s.comments = [{"id": "a", "text": "for the agent", "added_at": 1.0, "target": "worker"},
                  {"id": "b", "text": "for the planner", "added_at": 2.0, "target": "pm"}]
    substrate.save_sprint(s)
    ctx = Worker(substrate, FakeAgent())._build_context(substrate.load_sprint("sp1"))
    assert ctx.human_comments == ["for the agent"]


def test_repeated_failures_cap_out_to_failed(substrate):
    # A deterministically broken sprint must stop relaunching after the cap.
    from coscience.worker import MAX_AGENT_FAILURES
    agent = FakeAgent(result="boom: ImportError no sympy", status="failed")
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    # each attempt is 2 beats (launch, then collect-fail); run up to just before the cap
    for _ in range((MAX_AGENT_FAILURES - 1) * 2 + 1):
        worker.run_one_beat()
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING   # not yet
    out = worker.run_one_beat()                                            # the capping failure
    assert out == BeatOutcome.COMPLETED
    sp = substrate.load_sprint("sp1")
    assert sp.status == SprintStatus.FAILED
    prog = substrate.load_progress("sp1")
    assert prog.failures == MAX_AGENT_FAILURES
    assert "ImportError" in prog.last_error
    assert prog.agent_token == ""


def test_usage_limit_message_is_not_a_result(substrate):
    # The classic dead-on-arrival case: agent printed the limit message and exited 1.
    agent = FakeAgent(result="You've hit your session limit · resets 6:40am", status="failed")
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent)
    worker.run_one_beat()
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert substrate.load_sprint("sp1").results == []


def test_usage_exhausted_blocks_launch(substrate):
    agent = FakeAgent()
    substrate.save_sprint(_approved("sp1"))
    worker = Worker(substrate, agent, usage_gate=lambda: False)
    assert worker.run_one_beat() == BeatOutcome.IDLE       # claimed but not launched
    assert agent.started == []                             # no agent spawned
    assert substrate.load_progress("sp1").agent_token == ""


def test_usage_recovers_then_launches(substrate):
    agent = FakeAgent()
    substrate.save_sprint(_approved("sp1"))
    gate = {"ok": False}
    worker = Worker(substrate, agent, usage_gate=lambda: gate["ok"])
    assert worker.run_one_beat() == BeatOutcome.IDLE       # blocked
    gate["ok"] = True
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # now launches
    assert agent.started == ["sp1"]


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


def test_agent_runs_in_the_program_workdir_when_set(substrate, tmp_path):
    # A program pointing at its own project folder -> the agent launches there,
    # not in the control repo (metadata still lives in the control repo).
    proj = tmp_path / "hobby-synth"; proj.mkdir()
    substrate.save_program(Program(id="p1", title="Hobby", goals="tinker",
                                   workdir=str(proj)))
    substrate.save_sprint(_approved("p1-c0-x", program="p1"))

    class Capturing(FakeAgent):
        def start(self, sprint, context, sprint_dir, repo_root=None):
            self.launched_in, self.ctx = repo_root, context
            return super().start(sprint, context, sprint_dir, repo_root)

    agent = Capturing()
    Worker(substrate, agent).run_sprint_beat(substrate.load_sprint("p1-c0-x"))
    assert str(agent.launched_in) == str(proj)             # agent cwd = project folder
    assert str(agent.ctx.repo_root) == str(proj)


def test_agent_falls_back_to_control_repo_when_workdir_missing(substrate, tmp_path):
    # A workdir that doesn't exist must not send the agent into a bad cwd; it falls
    # back to the control repo rather than failing to launch.
    substrate.save_program(Program(id="p1", title="Hobby", goals="tinker",
                                   workdir="/no/such/dir"))
    substrate.save_sprint(_approved("p1-c0-x", program="p1"))
    agent = FakeAgent()
    ctx = Worker(substrate, agent)._build_context(substrate.load_sprint("p1-c0-x"))
    assert ctx.repo_root == substrate.repo_root
