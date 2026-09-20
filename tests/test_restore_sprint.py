"""Undoing a cancel (P4). Cancel was the one human action with no way back: a misclick
cost the sprint's goals, plan, threads, votes and lineage, and the only recovery was to
propose it again by hand. A restore puts it back where it was canceled from.

A sprint reaches `canceled` four ways, and they do not all deserve the same way back."""
import pytest

from coscience.models import Program, Sprint, SprintStatus, set_status
from coscience.service import Service


def _svc(substrate, status=SprintStatus.PROPOSED, **kw):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", status=status, goals="g", plan=["x"],
                                 program="p", **kw))
    return Service(substrate.repo_root)


def _status(svc):
    return svc.get_sprint("s1")["status"]


def _walk(substrate, *steps):
    """A sprint with a faithful history: every status it really passed through, in
    order, the way the worker and dispatcher record them."""
    substrate.save_program(Program(id="p", title="P", goals="g"))
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["x"], program="p")
    set_status(s, SprintStatus.PROPOSED)
    for st in steps:
        set_status(s, st)
    substrate.save_sprint(s)
    return Service(substrate.repo_root)


# --- where each cancel goes back to ------------------------------------------------

def test_a_rejected_proposal_goes_back_to_proposed(substrate):
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.reject_sprint("s1", by="olegs")
    assert svc.restore_sprint("s1", by="olegs") == "proposed"
    assert _status(svc) == "proposed"


def test_a_rejected_approved_sprint_goes_back_to_approved(substrate):
    """Not to proposed: it had already been through review, and sending it back there
    would make the human approve it a second time."""
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.approve_sprint("s1", by="olegs")
    svc.reject_sprint("s1", by="olegs")
    assert svc.restore_sprint("s1", by="olegs") == "approved"


def test_a_rejected_queued_sprint_goes_back_to_queued(substrate):
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.approve_sprint("s1", by="olegs")
    svc.run_sprint("s1", by="olegs")
    svc.reject_sprint("s1", by="olegs")
    assert svc.restore_sprint("s1", by="olegs") == "queued"


def test_a_canceled_parked_sprint_goes_back_to_the_shelf(substrate):
    """Parked is where the human deliberately put it; restoring to proposed would push
    it back into the review pool and the PM's proposed cap."""
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.park_sprint("s1", by="olegs")
    svc.cancel_parked_sprint("s1", by="olegs")
    assert svc.restore_sprint("s1", by="olegs") == "parked"


def test_a_sprint_stopped_mid_run_is_re_queued(substrate):
    """Its agent is dead and its lease released, so there is no executing to return
    to — only a fresh run."""
    # the worker cancels it after a human stop
    svc = _walk(substrate, SprintStatus.QUEUED, SprintStatus.EXECUTING,
                SprintStatus.CANCELED)
    assert svc.restore_sprint("s1", by="olegs") == "queued"


def test_a_sprint_canceled_out_of_an_escalation_is_re_queued(substrate):
    svc = _walk(substrate, SprintStatus.QUEUED, SprintStatus.EXECUTING,
                SprintStatus.ESCALATED, SprintStatus.CANCELED)
    assert svc.restore_sprint("s1", by="olegs") == "queued"


def test_a_cancel_with_no_history_at_all_lands_in_proposed(substrate):
    """Legacy sprints carry no status_history; proposed is the safe floor — it runs
    nothing without a human approving it first."""
    svc = _svc(substrate, SprintStatus.CANCELED)
    assert svc.restore_sprint("s1", by="olegs") == "proposed"


# --- the one that must not come back -----------------------------------------------

def test_a_demoted_sprint_refuses_to_be_restored(substrate):
    """Demote turns the sprint into an idea and rewires the graph onto it. Restoring
    would leave the platform holding both."""
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.demote_sprint("s1", by="olegs")
    with pytest.raises(ValueError, match="demoted to an idea"):
        svc.restore_sprint("s1", by="olegs")
    assert _status(svc) == "canceled"


# --- what a restore has to clear ---------------------------------------------------

def test_a_restored_sprint_is_not_stopped_again_on_the_next_beat(substrate):
    """The stop that killed it is still recorded in progress. Left set, the very next
    dispatcher beat would cancel the restored sprint all over again."""
    svc = _walk(substrate, SprintStatus.QUEUED, SprintStatus.EXECUTING,
                SprintStatus.CANCELED)
    progress = substrate.load_progress("s1")
    progress.stop_requested = True
    substrate.save_progress(progress)

    svc.restore_sprint("s1", by="olegs")
    assert substrate.load_progress("s1").stop_requested is False


def test_a_pending_stop_is_cleared_even_on_a_pre_run_restore(substrate):
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.reject_sprint("s1", by="olegs")
    progress = substrate.load_progress("s1")
    progress.stop_requested = True
    substrate.save_progress(progress)

    svc.restore_sprint("s1", by="olegs")
    assert substrate.load_progress("s1").stop_requested is False


def test_a_re_queued_restore_starts_a_fresh_run(substrate):
    svc = _walk(substrate, SprintStatus.QUEUED, SprintStatus.EXECUTING,
                SprintStatus.CANCELED)
    (substrate.sprint_dir("s1") / "finished.json").write_text('{"summary": "half done"}')
    progress = substrate.load_progress("s1")
    progress.agent_session_id = "old-session"
    progress.agent_token = "tok"
    progress.failures = 3
    progress.escalation = {"level": "human"}
    progress.pm_answered = True
    substrate.save_progress(progress)

    svc.restore_sprint("s1", by="olegs")

    assert not (substrate.sprint_dir("s1") / "finished.json").exists()
    fresh = substrate.load_progress("s1")
    assert fresh.agent_session_id == ""
    assert fresh.agent_token == ""
    assert fresh.failures == 0
    assert fresh.escalation == {}
    assert fresh.pm_answered is False


def test_a_pre_run_restore_leaves_the_record_alone(substrate):
    """Nothing ran, so there is nothing to reset — and the sprint's own content must
    survive the round trip untouched. That is the whole point of restoring it."""
    svc = _svc(substrate, SprintStatus.PROPOSED, title="Measure the thing",
               rationale="because")
    svc.vote_sprint("s1", by="olegs", value=1)
    svc.reject_sprint("s1", by="olegs")
    svc.restore_sprint("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["title"] == "Measure the thing"
    assert d["goals"] == "g"
    assert d["plan"] == ["x"]
    assert d["rationale"] == "because"
    assert d["votes"]["up"] == 1


# --- who may do it -----------------------------------------------------------------

def test_restore_is_recorded_as_a_human_action(substrate):
    """So the program page does not highlight the restore back at whoever clicked it."""
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.reject_sprint("s1", by="olegs")
    svc.restore_sprint("s1", by="olegs")
    row = next(s for s in svc.get_program("p")["sprints"] if s["id"] == "s1")
    assert row["last_status_by"] == "human"


def test_the_restore_is_written_into_the_sprints_history(substrate):
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.reject_sprint("s1", by="olegs")
    svc.restore_sprint("s1", by="olegs")
    last = svc.get_sprint("s1")["status_history"][-1]
    assert last["action"] == "restore"
    assert last["by"] == "olegs"


# --- refusals ----------------------------------------------------------------------

@pytest.mark.parametrize("status", [SprintStatus.PROPOSED, SprintStatus.APPROVED,
                                    SprintStatus.QUEUED, SprintStatus.EXECUTING,
                                    SprintStatus.DONE, SprintStatus.FAILED,
                                    SprintStatus.PARKED])
def test_only_a_canceled_sprint_can_be_restored(substrate, status):
    svc = _svc(substrate, status)
    with pytest.raises(ValueError, match="can only restore a canceled sprint"):
        svc.restore_sprint("s1", by="olegs")


def test_restoring_something_that_is_not_there_is_a_not_found(substrate):
    from coscience.service import NotFoundError
    svc = _svc(substrate, SprintStatus.CANCELED)
    with pytest.raises(NotFoundError):
        svc.restore_sprint("nope", by="olegs")


def test_restoring_twice_refuses_the_second_time(substrate):
    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.reject_sprint("s1", by="olegs")
    svc.restore_sprint("s1", by="olegs")
    with pytest.raises(ValueError, match="can only restore a canceled sprint"):
        svc.restore_sprint("s1", by="olegs")


# --- over HTTP ---------------------------------------------------------------------

def test_the_restore_route_returns_the_sprint_in_its_new_status(substrate):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app

    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.approve_sprint("s1", by="olegs")
    svc.reject_sprint("s1", by="olegs")
    client = TestClient(build_app(svc))

    r = client.post("/api/sprints/s1/restore")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_the_restore_route_404s_on_a_sprint_that_is_not_there(substrate):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app

    client = TestClient(build_app(_svc(substrate, SprintStatus.CANCELED)))
    assert client.post("/api/sprints/nope/restore").status_code == 404


def test_the_restore_route_422s_with_the_reason_it_refused(substrate):
    """The reason has to reach the human: "it was demoted" is actionable, a bare
    failure is not."""
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app

    svc = _svc(substrate, SprintStatus.PROPOSED)
    svc.demote_sprint("s1", by="olegs")
    client = TestClient(build_app(svc))

    r = client.post("/api/sprints/s1/restore")
    assert r.status_code == 422
    assert "demoted to an idea" in r.json()["detail"]
