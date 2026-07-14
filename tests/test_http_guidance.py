import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    c = TestClient(build_app(svc))
    c.svc = svc
    return c


def test_add_list_delete_guidance(client):
    r = client.post("/api/programs/p1/guidance", json={"text": "focus on assays"})
    assert r.status_code == 201
    thread = r.json()
    assert thread["messages"][0]["text"] == "focus on assays"
    assert thread["target"] == "pm"

    r = client.get("/api/programs/p1/guidance")
    assert [t["id"] for t in r.json()] == [thread["id"]]

    assert client.delete(f"/api/programs/p1/guidance/{thread['id']}").status_code == 204
    assert client.get("/api/programs/p1/guidance").json() == []


def test_guidance_thread_reply_complete_seen(client):
    tid = client.post("/api/programs/p1/guidance", json={"text": "first"}).json()["id"]
    r = client.post(f"/api/programs/p1/guidance/{tid}/complete")
    assert r.status_code == 200 and r.json()["status"] == "complete"

    r = client.post("/api/programs/p1/guidance", json={"text": "more", "thread_id": tid})
    assert r.status_code == 201
    got = client.get("/api/programs/p1/guidance").json()[0]
    assert len(got["messages"]) == 2
    assert got["status"] == "open"          # reopened by the new human message

    assert client.post(f"/api/programs/p1/guidance/{tid}/seen").status_code == 200


def test_guidance_thread_not_found(client):
    assert client.post("/api/programs/p1/guidance/ghost/complete").status_code == 404
    assert client.post("/api/programs/p1/guidance/ghost/seen").status_code == 404


def test_empty_guidance_text_is_422(client):
    assert client.post("/api/programs/p1/guidance", json={"text": ""}).status_code == 422


def test_delete_unknown_thread_is_204(client):
    assert client.delete("/api/programs/p1/guidance/nope").status_code == 204


def test_guidance_missing_program_is_404(client):
    assert client.get("/api/programs/nope/guidance").status_code == 404
    assert client.post("/api/programs/nope/guidance", json={"text": "x"}).status_code == 404
    assert client.delete("/api/programs/nope/guidance/x").status_code == 404
