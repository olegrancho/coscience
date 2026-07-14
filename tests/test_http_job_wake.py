import time
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus, ProgressState


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"], program="p1")
    svc.substrate.save_sprint(s)
    return svc


def test_sleeping_state_and_wake(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", job_token="1:1", job_note="train",
        job_out="j.out", job_started_at=time.time(), job_next_wake=time.time() + 9999,
        job_max_seconds=9999))
    c = TestClient(build_app(svc))
    got = c.get("/api/sprints/s1").json()
    assert got["agent_state"] == "sleeping" and got["job"]["note"] == "train"
    assert c.post("/api/sprints/s1/wake").status_code == 200
    assert svc.substrate.load_progress("s1").job_next_wake <= time.time() + 1
