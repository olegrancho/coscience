from coscience.models import (
    BeatOutcome,
    ProgressState,
    Result,
    Sprint,
    SprintStatus,
    Step,
    StepResult,
)


def test_step_from_dict():
    step = Step.from_dict({"id": "s1", "run": "echo hi"})
    assert step == Step(id="s1", run="echo hi")


def test_sprint_defaults():
    sprint = Sprint(
        id="sp1",
        status=SprintStatus.APPROVED,
        goals="cure",
        plan=[Step("s1", "echo hi")],
    )
    assert sprint.program is None
    assert sprint.results == []


def test_progress_defaults_are_independent():
    a = ProgressState(sprint_id="sp1")
    b = ProgressState(sprint_id="sp2")
    a.completed_steps.append("s1")
    assert b.completed_steps == []  # no shared mutable default


def test_status_is_string_valued():
    assert SprintStatus.APPROVED == "approved"
    assert BeatOutcome.COMPLETED == "completed"


def test_stepresult_and_result_construct():
    assert StepResult("s1", True).output == ""
    assert Result("r1", "sp1", "did a thing").summary == "did a thing"
