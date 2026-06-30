import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program, Result
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    client = TestClient(build_app(svc))
    client.svc = svc  # expose for seeding results
    return client


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_returns_sha(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    sha = r.json()["sha"]
    assert isinstance(sha, str) and sha


def test_sprint_files_endpoint(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g", "plan": ["a"]})
    (client.svc.substrate.sprint_dir("sp1") / "scratchpad.md").write_text("# notes")
    r = client.get("/api/sprints/sp1/files")
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert names == ["scratchpad.md"]


def test_sprint_files_missing_is_404(client):
    assert client.get("/api/sprints/nope/files").status_code == 404


def test_submit_then_get_and_list(client):
    body = {"id": "sp1", "goals": "cure",
            "plan": ["echo hi"],
            "priority": 3, "resources_required": {"gpu": 1}}
    r = client.post("/api/sprints", json=body)
    assert r.status_code == 201
    created = r.json()
    assert created["id"] == "sp1"
    assert created["status"] == "proposed"
    assert created["plan"] == ["echo hi"]

    r = client.get("/api/sprints", params={"status": "proposed"})
    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == ["sp1"]

    r = client.get("/api/sprints/sp1")
    assert r.status_code == 200
    assert r.json()["priority"] == 3
    assert r.json()["lease"] is None


def test_approve_changes_status(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": ["true"]})
    r = client.post("/api/sprints/sp1/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert client.get("/api/sprints", params={"status": "proposed"}).json() == []


def test_results_round_trip(client):
    client.svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    rows = client.get("/api/results").json()
    assert isinstance(rows[0].pop("completed_at"), float)
    assert rows == [{"id": "r1", "sprint": "sp1", "summary": "found X"}]
    assert client.get("/api/results/r1").json()["summary"] == "found X"


def test_ledger_status_shape(client):
    body = client.get("/api/ledger").json()
    assert set(body) == {"capacity", "used", "available", "leases"}


def test_missing_sprint_is_404(client):
    assert client.get("/api/sprints/nope").status_code == 404


def test_approve_missing_is_404(client):
    assert client.post("/api/sprints/nope/approve").status_code == 404


def test_missing_result_is_404(client):
    assert client.get("/api/results/nope").status_code == 404


def test_duplicate_submit_is_409(client):
    body = {"id": "sp1", "goals": "g", "plan": ["true"]}
    assert client.post("/api/sprints", json=body).status_code == 201
    assert client.post("/api/sprints", json=body).status_code == 409


def test_empty_plan_is_422(client):
    assert client.post("/api/sprints", json={"id": "sp1", "goals": "g", "plan": []}).status_code == 422


def test_invalid_status_filter_is_422(client):
    assert client.get("/api/sprints", params={"status": "bogus"}).status_code == 422


def test_reject_via_http(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": ["true"]})
    r = client.post("/api/sprints/sp1/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "canceled"


def test_reject_non_proposed_is_422(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": ["true"]})
    client.post("/api/sprints/sp1/approve")
    assert client.post("/api/sprints/sp1/reject").status_code == 422


def test_reject_missing_is_404(client):
    assert client.post("/api/sprints/nope/reject").status_code == 404


def test_patch_priority(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": ["true"]})
    r = client.patch("/api/sprints/sp1", json={"priority": 8})
    assert r.status_code == 200
    assert r.json()["priority"] == 8


def test_patch_goals_when_approved_is_422(client):
    client.post("/api/sprints", json={"id": "sp1", "goals": "g",
                                      "plan": ["true"]})
    client.post("/api/sprints/sp1/approve")
    assert client.patch("/api/sprints/sp1", json={"goals": "x"}).status_code == 422


def test_patch_missing_is_404(client):
    assert client.patch("/api/sprints/nope", json={"priority": 1}).status_code == 404


def test_set_program_status_via_http(client):
    client.svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    r = client.post("/api/programs/p1/status", json={"status": "paused"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"


def test_set_program_status_invalid_is_422(client):
    client.svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    assert client.post("/api/programs/p1/status", json={"status": "bogus"}).status_code == 422


def test_set_program_status_missing_is_404(client):
    assert client.post("/api/programs/nope/status", json={"status": "paused"}).status_code == 404
