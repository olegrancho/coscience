"""Manual Resume: re-open a done/failed sprint for more work (clears its result,
resets counters, re-queues so the worker relaunches)."""
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import (Program, ProgramStatus, ProgressState, Result,
                              Sprint, SprintStatus)
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g",
                                       status=ProgramStatus.ACTIVE))
    return svc


def _done(svc, sid="p1-s"):
    svc.substrate.save_result(Result(id=f"{sid}-result", sprint=sid, summary="premature"))
    svc.substrate.save_sprint(Sprint(id=sid, status=SprintStatus.DONE, goals="g",
                                     program="p1", results=[f"{sid}-result"]))
    return sid


def test_resume_requeues_and_clears_result(tmp_path):
    svc = _svc(tmp_path)
    sid = _done(svc)
    svc.resume_sprint(sid, by="u")
    sp = svc.substrate.load_sprint(sid)
    assert sp.status == SprintStatus.QUEUED
    assert sp.results == []
    with pytest.raises(Exception):
        svc.substrate.load_result(f"{sid}-result")          # result file removed


def test_resume_resets_counters(tmp_path):
    svc = _svc(tmp_path)
    sid = _done(svc)
    svc.substrate.save_progress(ProgressState(
        sprint_id=sid, failures=2, ambiguous_exits=3, scratch_size=99,
        agent_token="stale", agent_session_id="old-sess", last_error="boom"))
    svc.resume_sprint(sid, by="u")
    prog = svc.substrate.load_progress(sid)
    assert prog.failures == 0 and prog.ambiguous_exits == 0 and prog.agent_token == ""
    assert prog.scratch_size == 0 and prog.agent_session_id == "" and prog.last_error == ""


def test_resume_allows_failed(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="f", status=SprintStatus.FAILED, goals="g", program="p1"))
    svc.resume_sprint("f", by="u")
    assert svc.substrate.load_sprint("f").status == SprintStatus.QUEUED


def test_resume_rejects_non_terminal(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="ex", status=SprintStatus.EXECUTING, goals="g", program="p1"))
    with pytest.raises(ValueError):
        svc.resume_sprint("ex", by="u")


def test_resume_clears_finished_sentinel(tmp_path):
    svc = _svc(tmp_path)
    sid = _done(svc)
    sd = svc.substrate.sprint_dir(sid)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "finished.json").write_text("{}")
    svc.resume_sprint(sid, by="u")
    assert not (sd / "finished.json").exists()


def test_resume_endpoint(tmp_path):
    svc = _svc(tmp_path)
    sid = _done(svc)
    client = TestClient(build_app(svc))
    r = client.post(f"/api/sprints/{sid}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    r2 = client.post("/api/sprints/nope/resume")
    assert r2.status_code == 404
