from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus


def _c(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"], program="p1"))
    return TestClient(build_app(svc)), svc


# --- sprint threads ---

def test_sprint_thread_reopen(tmp_path):
    c, svc = _c(tmp_path)
    tid = c.post("/api/sprints/s1/comments", json={"text": "cpu please", "target": "pm"}).json()["id"]
    assert c.post(f"/api/sprints/s1/threads/{tid}/complete").status_code == 200
    assert c.get("/api/sprints/s1").json()["threads"][0]["status"] == "complete"
    r = c.post(f"/api/sprints/s1/threads/{tid}/reopen")
    assert r.status_code == 200
    assert r.json()["status"] == "open"
    assert c.get("/api/sprints/s1").json()["threads"][0]["status"] == "open"


def test_sprint_thread_delete(tmp_path):
    c, svc = _c(tmp_path)
    tid = c.post("/api/sprints/s1/comments", json={"text": "cpu please", "target": "pm"}).json()["id"]
    assert len(c.get("/api/sprints/s1").json()["threads"]) == 1
    r = c.delete(f"/api/sprints/s1/threads/{tid}")
    assert r.status_code == 204
    assert c.get("/api/sprints/s1").json()["threads"] == []


def test_sprint_thread_reopen_and_delete_not_found(tmp_path):
    c, svc = _c(tmp_path)
    assert c.post("/api/sprints/s1/threads/ghost/reopen").status_code == 404
    assert c.delete("/api/sprints/s1/threads/ghost").status_code == 404
    assert c.post("/api/sprints/ghost/threads/ghost/reopen").status_code == 404
    assert c.delete("/api/sprints/ghost/threads/ghost").status_code == 404


# --- idea threads ---

def test_idea_thread_reopen(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "an idea", source="human")
    tid = c.post(f"/api/programs/p1/ideas/{idea['id']}/comments", json={"text": "refine this"}).json()["id"]
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}/complete").status_code == 200
    r = c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}/reopen")
    assert r.status_code == 200
    assert r.json()["status"] == "open"
    assert c.get("/api/programs/p1/ideas").json()["ideas"][0]["threads"][0]["status"] == "open"


def test_idea_thread_delete(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "an idea", source="human")
    tid = c.post(f"/api/programs/p1/ideas/{idea['id']}/comments", json={"text": "refine this"}).json()["id"]
    r = c.delete(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}")
    assert r.status_code == 204
    assert c.get("/api/programs/p1/ideas").json()["ideas"][0]["threads"] == []


def test_idea_thread_reopen_and_delete_not_found(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "an idea", source="human")
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/ghost/reopen").status_code == 404
    assert c.delete(f"/api/programs/p1/ideas/{idea['id']}/threads/ghost").status_code == 404
    assert c.post("/api/programs/p1/ideas/ghost/threads/ghost/reopen").status_code == 404
    assert c.delete("/api/programs/p1/ideas/ghost/threads/ghost").status_code == 404


# --- guidance threads ---

def test_guidance_thread_reopen(tmp_path):
    c, svc = _c(tmp_path)
    tid = c.post("/api/programs/p1/guidance", json={"text": "prefer cheap models"}).json()["id"]
    assert c.post(f"/api/programs/p1/guidance/{tid}/complete").status_code == 200
    r = c.post(f"/api/programs/p1/guidance/{tid}/reopen")
    assert r.status_code == 200
    assert r.json()["status"] == "open"
    assert c.get("/api/programs/p1/guidance").json()[0]["status"] == "open"


def test_guidance_thread_delete_reuses_remove_guidance(tmp_path):
    c, svc = _c(tmp_path)
    tid = c.post("/api/programs/p1/guidance", json={"text": "prefer cheap models"}).json()["id"]
    r = c.delete(f"/api/programs/p1/guidance/{tid}")
    assert r.status_code == 204
    assert c.get("/api/programs/p1/guidance").json() == []


def test_guidance_thread_reopen_not_found(tmp_path):
    c, svc = _c(tmp_path)
    assert c.post("/api/programs/p1/guidance/ghost/reopen").status_code == 404
    assert c.post("/api/programs/ghost/guidance/ghost/reopen").status_code == 404
