from coscience.models import (
    BeatOutcome,
    ProgressState,
    Result,
    Sprint,
    SprintStatus,
)


def test_sprint_defaults():
    sprint = Sprint(id="sp1", status=SprintStatus.APPROVED, goals="cure",
                    plan=["scan the primes", "tabulate the gaps"])
    assert sprint.program is None
    assert sprint.results == []
    assert sprint.plan == ["scan the primes", "tabulate the gaps"]


def test_sprint_plan_defaults_empty():
    assert Sprint(id="s", status=SprintStatus.PROPOSED, goals="g").plan == []


def test_progress_defaults_track_the_agent_run():
    p = ProgressState(sprint_id="sp1")
    assert p.agent_token == ""
    assert p.started_at is None


def test_status_is_string_valued():
    assert SprintStatus.APPROVED == "approved"
    assert BeatOutcome.COMPLETED == "completed"


def test_result_constructs():
    assert Result("r1", "sp1", "did a thing").summary == "did a thing"
