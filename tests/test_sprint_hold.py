"""The planner's hold (E1): "not yet" said on an approved sprint, without spending the
human authorization the planner has no power to give back."""
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program, Sprint, SprintStatus
from coscience.service import NotFoundError, Service


def _svc(substrate, status=SprintStatus.APPROVED, hold=None):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", status=status, goals="g", plan=["x"],
                                 program="p", hold=hold or {}))
    return Service(substrate.repo_root)


_HOLD = {"why": "waiting on the recovery run", "at": 1.0, "by": "pm"}


def test_the_hold_reaches_the_sprint_page(substrate):
    svc = _svc(substrate, hold=_HOLD)
    d = svc.get_sprint("s1")
    assert d["status"] == "approved"          # a hold never moves the sprint
    assert d["hold"]["why"] == "waiting on the recovery run"
    assert d["hold"]["by"] == "pm"


def test_the_hold_reaches_the_program_list(substrate):
    """So a held experiment is visible without opening it."""
    svc = _svc(substrate, hold=_HOLD)
    row = next(s for s in svc.get_program("p")["sprints"] if s["id"] == "s1")
    assert row["hold"]["why"] == "waiting on the recovery run"


def test_a_sprint_with_no_hold_reports_an_empty_one(substrate):
    assert _svc(substrate).get_sprint("s1")["hold"] == {}


def test_a_human_clears_the_hold_and_the_sprint_stays_approved(substrate):
    svc = _svc(substrate, hold=_HOLD)
    svc.clear_sprint_hold("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["hold"] == {}
    assert d["status"] == "approved"           # clearing is not a lifecycle transition


def test_clearing_a_hold_writes_no_status_history(substrate):
    """The sprint did not move, so nothing belongs in its lifecycle timeline — and the
    program page must not highlight a row because a hold was lifted."""
    svc = _svc(substrate, hold=_HOLD)
    before = len(svc.get_sprint("s1")["status_history"])
    svc.clear_sprint_hold("s1", by="olegs")
    assert len(svc.get_sprint("s1")["status_history"]) == before


def test_clearing_a_hold_that_is_not_there_refuses(substrate):
    with pytest.raises(ValueError, match="is not held"):
        _svc(substrate).clear_sprint_hold("s1", by="olegs")


def test_clearing_a_hold_on_nothing_is_a_not_found(substrate):
    with pytest.raises(NotFoundError):
        _svc(substrate).clear_sprint_hold("nope", by="olegs")


def test_running_a_held_sprint_clears_the_hold(substrate):
    """A human overriding the hold is the answer to it, not a state to carry forward."""
    svc = _svc(substrate, hold=_HOLD)
    svc.run_sprint("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["status"] == "queued" and d["hold"] == {}


def test_sending_a_held_sprint_back_clears_the_hold(substrate):
    """It is no longer approved, so there is nothing left to hold."""
    svc = _svc(substrate, hold=_HOLD)
    svc.send_back_sprint("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["status"] == "proposed" and d["hold"] == {}


def test_a_hold_survives_a_save_and_load(substrate):
    svc = _svc(substrate, hold=_HOLD)
    assert substrate.load_sprint("s1").hold == _HOLD


def test_the_clear_route_returns_the_sprint(substrate):
    client = TestClient(build_app(_svc(substrate, hold=_HOLD)))
    r = client.post("/api/sprints/s1/hold/clear")
    assert r.status_code == 200
    assert r.json()["hold"] == {} and r.json()["status"] == "approved"


def test_the_clear_route_404s_and_422s(substrate):
    client = TestClient(build_app(_svc(substrate)))
    assert client.post("/api/sprints/nope/hold/clear").status_code == 404
    r = client.post("/api/sprints/s1/hold/clear")
    assert r.status_code == 422
    assert "not held" in r.json()["detail"]
