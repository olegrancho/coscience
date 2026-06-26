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
    note = r.json()
    assert note["text"] == "focus on assays"

    r = client.get("/api/programs/p1/guidance")
    assert [n["id"] for n in r.json()] == [note["id"]]

    assert client.delete(f"/api/programs/p1/guidance/{note['id']}").status_code == 204
    assert client.get("/api/programs/p1/guidance").json() == []


def test_delete_unknown_note_is_204(client):
    assert client.delete("/api/programs/p1/guidance/nope").status_code == 204


def test_guidance_missing_program_is_404(client):
    assert client.get("/api/programs/nope/guidance").status_code == 404
    assert client.post("/api/programs/nope/guidance", json={"text": "x"}).status_code == 404
    assert client.delete("/api/programs/nope/guidance/x").status_code == 404
