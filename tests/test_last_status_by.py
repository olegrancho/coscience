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


# --- who proposed it (P3, found at QC) ---------------------------------------------

def test_a_sprint_the_pm_proposed_is_the_pms(substrate):
    """A new proposal is exactly what should announce itself on the program page. It
    used to be born with an anonymous history entry, indistinguishable from one the
    human wrote in the dashboard, so it read as "platform" and could never be told
    apart from the viewer's own."""
    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint

    substrate.save_program(Program(id="p", title="P", goals="g"))
    pm_beat(substrate, "p", FakeReasoner([PMCycleOutput(report="r", proposals=[
        ProposedSprint(suffix="idea", goals="do the thing", plan=["x"])])]), force=True)
    svc = Service(substrate.repo_root)
    row = next(s for s in svc.get_program("p")["sprints"])
    assert row["status"] == "proposed"
    assert row["last_status_by"] == "pm"


def test_a_sprint_the_human_wrote_is_theirs(substrate):
    """And must not light up at the person who just typed it."""
    substrate.save_program(Program(id="p", title="P", goals="g"))
    svc = Service(substrate.repo_root)
    svc.submit_sprint(id="p-mine", goals="g", plan=["x"], program="p", by="olegs")
    row = next(s for s in svc.get_program("p")["sprints"] if s["id"] == "p-mine")
    assert row["last_status_by"] == "human"


def test_a_sprint_written_with_no_username_is_still_the_humans(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    svc = Service(substrate.repo_root)
    svc.submit_sprint(id="p-mine", goals="g", plan=["x"], program="p")
    row = next(s for s in svc.get_program("p")["sprints"] if s["id"] == "p-mine")
    assert row["last_status_by"] == "human"


def test_the_birth_entry_is_not_duplicated_on_later_saves(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    svc = Service(substrate.repo_root)
    svc.submit_sprint(id="p-mine", goals="g", plan=["x"], program="p", by="olegs")
    svc.approve_sprint("p-mine", by="olegs")
    history = svc.get_sprint("p-mine")["status_history"]
    assert [h["status"] for h in history] == ["proposed", "approved"]
