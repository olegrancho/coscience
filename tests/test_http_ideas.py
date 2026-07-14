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


def test_add_list_delete_idea(client):
    r = client.post("/api/programs/p1/ideas", json={"text": "try a wheel sieve"})
    assert r.status_code == 201
    idea = r.json()
    assert idea["source"] == "human" and idea["protected"] is True

    pool = client.get("/api/programs/p1/ideas").json()
    assert [i["id"] for i in pool["ideas"]] == [idea["id"]]

    assert client.delete(f"/api/programs/p1/ideas/{idea['id']}").status_code == 204
    assert client.get("/api/programs/p1/ideas").json()["ideas"] == []


def test_pin_and_comment(client):
    iid = client.post("/api/programs/p1/ideas", json={"text": "lead"}).json()["id"]
    r = client.post(f"/api/programs/p1/ideas/{iid}/pin", json={"pinned": True})
    assert r.status_code == 200 and r.json()["pinned"] is True
    r = client.post(f"/api/programs/p1/ideas/{iid}/comments", json={"text": "keep it"})
    assert r.status_code == 201
    assert r.json()["messages"][0]["text"] == "keep it"
    pool = client.get("/api/programs/p1/ideas").json()["ideas"]
    idea = next(i for i in pool if i["id"] == iid)
    assert idea["threads"][0]["messages"][0]["text"] == "keep it"


def test_empty_idea_is_422(client):
    assert client.post("/api/programs/p1/ideas", json={"text": ""}).status_code == 422


def test_missing_program_is_404(client):
    assert client.get("/api/programs/nope/ideas").status_code == 404
    assert client.post("/api/programs/nope/ideas", json={"text": "x"}).status_code == 404
