from coscience.models import Program, ProgramStatus
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.pm_runner import pm_loop, pm_run_once


def _out(suffix):
    return PMCycleOutput(proposals=[ProposedSprint(suffix=suffix, goals="g",
                                                   plan=[{"id": "s", "run": "true"}])],
                         report="r")


def test_run_once_beats_only_active_programs(substrate):
    substrate.save_program(Program(id="a", title="A", goals="x"))
    substrate.save_program(Program(id="b", title="B", goals="y",
                                   status=ProgramStatus.PAUSED))
    fake = FakeReasoner([_out("z"), _out("z")])
    summaries = pm_run_once(substrate, fake)
    assert [s["program"] for s in summaries] == ["a"]      # only the active one
    assert substrate.load_sprint("a-c0-z").program == "a"


def test_pm_loop_runs_max_rounds_with_injected_sleep(substrate):
    substrate.save_program(Program(id="a", title="A", goals="x"))
    fake = FakeReasoner([_out("p"), _out("q")])
    sleeps = []
    rounds = pm_loop(substrate, fake, interval=9.0, max_rounds=2,
                     sleep=lambda s: sleeps.append(s))
    assert rounds == 2
    # event-driven: round 1 reasons+proposes (cycle -> 1); round 2 sees no change, skips
    assert substrate.load_pm_state("a").cycle == 1
    assert sleeps == [9.0]                                 # slept between, not after last
