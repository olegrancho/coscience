from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus


def _c(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"], program="p1"))
    return TestClient(build_app(svc)), svc


def test_start_append_complete_seen(tmp_path):
    c, svc = _c(tmp_path)
    r = c.post("/api/sprints/s1/comments", json={"text": "cpu please", "target": "pm"})
    tid = r.json()["id"]
    assert r.status_code == 201 and r.json()["messages"][0]["text"] == "cpu please"
    # simulate a PM reply landing on the thread
    s = svc.substrate.load_sprint("s1");
    from coscience import threads as th; th.append(s.threads[0], "pm", "done", "", now=2.0)
    svc.substrate.save_sprint(s)
    got = c.get("/api/sprints/s1").json()
    assert got["threads"][0]["agent_unseen"] is True
    assert c.post(f"/api/sprints/s1/threads/{tid}/seen").status_code == 200
    assert c.get("/api/sprints/s1").json()["threads"][0]["agent_unseen"] is False
    c.post("/api/sprints/s1/comments", json={"text": "more", "target": "pm", "thread_id": tid})
    assert len(c.get("/api/sprints/s1").json()["threads"][0]["messages"]) == 3
    assert c.post(f"/api/sprints/s1/threads/{tid}/complete").status_code == 200
    assert c.get("/api/sprints/s1").json()["threads"][0]["status"] == "complete"
