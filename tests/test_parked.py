"""Parked sprint status: human shelf for proposed sprints that frees the PM cap."""
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import MAX_PROPOSED, gather_context
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def _proposed(svc, sid="p1-s"):
    svc.substrate.save_sprint(Sprint(id=sid, status=SprintStatus.PROPOSED, goals="g", program="p1"))
    return sid


def test_park_then_unpark_roundtrip(tmp_path):
    svc = _svc(tmp_path); sid = _proposed(svc)
    svc.park_sprint(sid, by="u")
    assert svc.substrate.load_sprint(sid).status == SprintStatus.PARKED
    svc.unpark_sprint(sid, by="u")
    assert svc.substrate.load_sprint(sid).status == SprintStatus.PROPOSED


def test_park_requires_proposed(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="ex", status=SprintStatus.EXECUTING, goals="g", program="p1"))
    with pytest.raises(ValueError):
        svc.park_sprint("ex", by="u")


def test_cancel_parked_soft_cancels(tmp_path):
    svc = _svc(tmp_path); sid = _proposed(svc)
    svc.park_sprint(sid, by="u")
    svc.cancel_parked_sprint(sid, by="u")
    sp = svc.substrate.load_sprint(sid)
    assert sp.status == SprintStatus.CANCELED
    assert sp.status_history[-1]["action"] == "cancel"


def test_cancel_requires_parked(tmp_path):
    svc = _svc(tmp_path); sid = _proposed(svc)
    with pytest.raises(ValueError):
        svc.cancel_parked_sprint(sid, by="u")    # still proposed, not parked


def test_demote_from_parked(tmp_path):
    svc = _svc(tmp_path); sid = _proposed(svc)
    svc.park_sprint(sid, by="u")
    out = svc.demote_sprint(sid, by="u")
    assert svc.substrate.load_sprint(sid).status == SprintStatus.CANCELED
    assert out["idea"]["demoted"] is True


def test_parked_does_not_count_against_pm_cap(tmp_path):
    svc = _svc(tmp_path)
    # Fill the cap with proposed sprints, then park one -> a slot frees.
    for i in range(MAX_PROPOSED):
        svc.substrate.save_sprint(Sprint(id=f"p1-s{i}", status=SprintStatus.PROPOSED, goals="g", program="p1"))
    ctx = gather_context(svc.substrate, "p1")
    assert ctx.proposed_count == MAX_PROPOSED and ctx.free_slots == 0
    svc.park_sprint("p1-s0", by="u")
    ctx2 = gather_context(svc.substrate, "p1")
    assert ctx2.proposed_count == MAX_PROPOSED - 1 and ctx2.free_slots == 1
    # parked sprint is not in the PM's open-sprint view at all
    assert all(s["id"] != "p1-s0" for s in ctx2.open_sprints)


def test_parked_sprint_hidden_from_pm_feedback(tmp_path):
    from coscience import threads
    svc = _svc(tmp_path); sid = _proposed(svc)
    sp = svc.substrate.load_sprint(sid)
    sp.threads.append(threads.new_thread("pm", "please tweak", "u", now=1.0))
    svc.substrate.save_sprint(sp)
    assert any(f["sprint_id"] == sid for f in gather_context(svc.substrate, "p1").sprint_feedback)
    svc.park_sprint(sid, by="u")
    # parked -> the PM must not see the sprint or its open thread
    assert all(f["sprint_id"] != sid for f in gather_context(svc.substrate, "p1").sprint_feedback)


def test_get_graph_carries_status_and_keeps_parked(tmp_path):
    svc = _svc(tmp_path); sid = _proposed(svc)
    svc.park_sprint(sid, by="u")
    g = svc.get_graph("p1")
    node = next(n for n in g["nodes"] if n["id"] == sid)
    assert node["status"] == "parked"            # status surfaced for dimming
    assert node["stage"] == "experiment"         # parked is not excluded from the graph


def test_park_unpark_cancel_http(tmp_path):
    svc = _svc(tmp_path); sid = _proposed(svc)
    c = TestClient(build_app(svc))
    assert c.post(f"/api/sprints/{sid}/park").json()["status"] == "parked"
    assert c.post(f"/api/sprints/{sid}/unpark").json()["status"] == "proposed"
    c.post(f"/api/sprints/{sid}/park")
    assert c.post(f"/api/sprints/{sid}/cancel").json()["status"] == "canceled"
    # wrong status -> 422; missing -> 404
    assert c.post(f"/api/sprints/{sid}/park").status_code == 422   # already canceled
    assert c.post("/api/sprints/nope/park").status_code == 404
