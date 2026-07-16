from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import Service


def _client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1"))
    return svc, TestClient(build_app(svc))


def test_add_and_read_and_delete_edge(tmp_path):
    svc, c = _client(tmp_path)
    r = c.post("/api/programs/p1/edges",
               json={"type": "builds_on", "src": "s2", "dst": "s1", "rationale": "uses it"})
    assert r.status_code == 200
    edge = r.json()
    assert (edge["type"], edge["src"], edge["dst"], edge["source"]) == ("builds_on", "s2", "s1", "human")

    g = c.get("/api/programs/p1/graph").json()
    assert any(e["id"] == edge["id"] for e in g["edges"])
    assert {n["id"] for n in g["nodes"]} >= {"s1", "s2"}

    d = c.delete(f"/api/programs/p1/edges/{edge['id']}")
    assert d.status_code == 200
    assert svc.substrate.load_sprint("s2").edges == []


def test_add_invalid_edge_is_422(tmp_path):
    _svc, c = _client(tmp_path)
    r = c.post("/api/programs/p1/edges",
               json={"type": "builds_on", "src": "s2", "dst": "ghost", "rationale": "x"})
    assert r.status_code == 422


def test_add_edge_unknown_program_is_404(tmp_path):
    _svc, c = _client(tmp_path)
    r = c.post("/api/programs/nope/edges",
               json={"type": "builds_on", "src": "s2", "dst": "s1", "rationale": "x"})
    assert r.status_code == 404
