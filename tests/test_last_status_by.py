"""The actor of the last transition, on the payloads the dashboard highlights from (P3).
Driven through the real service calls, not by hand-writing history: the point is that a
human's own click is recorded as a human's, whatever route it took."""
from coscience.models import Program, Sprint, SprintStatus, set_status
from coscience.service import Service


def _svc(substrate, **kw):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", goals="g", plan=["x"], program="p", **kw))
    return Service(substrate.repo_root)


def _row(svc):
    return next(s for s in svc.get_program("p")["sprints"] if s["id"] == "s1")


def test_program_page_says_who_last_moved_each_sprint(substrate):
    svc = _svc(substrate, status=SprintStatus.PROPOSED)
    svc.approve_sprint("s1", by="olegs")
    assert _row(svc)["last_status_by"] == "human"


def test_parking_is_the_human_who_parked_it(substrate):
    svc = _svc(substrate, status=SprintStatus.PROPOSED)
    svc.park_sprint("s1", by="olegs")
    assert _row(svc)["last_status_by"] == "human"


def test_rejecting_is_the_human_who_rejected_it(substrate):
    svc = _svc(substrate, status=SprintStatus.PROPOSED)
    svc.reject_sprint("s1", by="olegs")
    assert _row(svc)["last_status_by"] == "human"


def test_a_human_with_no_username_is_still_a_human(substrate):
    """There is no login on the dashboard, so this is the normal case."""
    svc = _svc(substrate, status=SprintStatus.PROPOSED)
    svc.approve_sprint("s1", by="")
    assert _row(svc)["last_status_by"] == "human"


def test_the_pm_releasing_a_sprint_is_the_pm(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    s = Sprint(id="s1", status=SprintStatus.APPROVED, goals="g", plan=["x"], program="p")
    set_status(s, SprintStatus.QUEUED, by="pm", action="run")
    substrate.save_sprint(s)
    assert _row(Service(substrate.repo_root))["last_status_by"] == "pm"


def test_a_worker_finishing_is_the_platform(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["x"], program="p")
    set_status(s, SprintStatus.DONE)
    substrate.save_sprint(s)
    assert _row(Service(substrate.repo_root))["last_status_by"] == "platform"


def test_a_worker_failing_is_the_platform(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["x"], program="p")
    set_status(s, SprintStatus.FAILED)
    substrate.save_sprint(s)
    assert _row(Service(substrate.repo_root))["last_status_by"] == "platform"


def test_a_sprint_that_was_never_moved_reads_as_the_platform(substrate):
    svc = _svc(substrate, status=SprintStatus.PROPOSED)
    assert _row(svc)["last_status_by"] == "platform"


def test_the_sprint_list_carries_the_actor_too(substrate):
    """Both payloads: the program page reads one, the sprints board the other."""
    svc = _svc(substrate, status=SprintStatus.PROPOSED)
    svc.approve_sprint("s1", by="olegs")
    row = next(s for s in svc.list_sprints() if s["id"] == "s1")
    assert row["last_status_by"] == "human"
