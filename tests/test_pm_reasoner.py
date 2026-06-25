from coscience.pm_reasoner import (FakeReasoner, PMContext, PMCycleOutput,
                                    ProposedSprint)


def _ctx():
    return PMContext(program_id="p1", goals="cure", cycle=0)


def test_fake_reasoner_returns_scripted_outputs_in_order():
    o1 = PMCycleOutput(proposals=[ProposedSprint(suffix="a", goals="g",
                                                 plan=[{"id": "s", "run": "true"}])],
                       report="r1")
    o2 = PMCycleOutput(report="r2")
    fake = FakeReasoner([o1, o2])
    assert fake.run(_ctx()) is o1
    assert fake.run(_ctx()) is o2


def test_fake_reasoner_records_calls():
    fake = FakeReasoner([PMCycleOutput()])
    ctx = _ctx()
    fake.run(ctx)
    assert fake.calls == [ctx]


def test_fake_reasoner_empty_when_exhausted():
    fake = FakeReasoner([])
    assert fake.run(_ctx()) == PMCycleOutput()
