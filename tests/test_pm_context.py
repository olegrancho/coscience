from coscience.models import (PMState, Program, Result, Sprint, SprintStatus, Step)
from coscience.pm_agent import gather_context


def test_gather_context_splits_open_and_completed(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure cancer"))
    substrate.save_pm_state(PMState(program_id="p1", cycle=2, proposed_ids=["p1-c0-a"]))
    substrate.save_sprint(Sprint(id="p1-open", status=SprintStatus.APPROVED,
                                 goals="assay", plan=[Step("s", "true")], program="p1"))
    substrate.save_sprint(Sprint(id="p1-done", status=SprintStatus.DONE, goals="prior",
                                 plan=[Step("s", "true")], program="p1",
                                 results=["p1-done-result"]))
    substrate.save_result(Result(id="p1-done-result", sprint="p1-done", summary="found X"))
    substrate.save_sprint(Sprint(id="other", status=SprintStatus.PROPOSED, goals="elsewhere",
                                 plan=[Step("s", "true")], program="p2"))

    ctx = gather_context(substrate, "p1")
    assert ctx.goals == "cure cancer"
    assert ctx.cycle == 2
    assert ctx.prior_proposals == ["p1-c0-a"]
    assert [s["id"] for s in ctx.open_sprints] == ["p1-open"]
    assert ctx.completed == [{"id": "p1-done", "goals": "prior", "result": "found X"}]


def test_gather_context_done_without_result(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    substrate.save_sprint(Sprint(id="p1-d", status=SprintStatus.DONE, goals="d",
                                 plan=[Step("s", "true")], program="p1"))
    ctx = gather_context(substrate, "p1")
    assert ctx.completed == [{"id": "p1-d", "goals": "d", "result": ""}]
