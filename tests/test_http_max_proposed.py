import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="A", goals="x"))
    return TestClient(build_app(svc))


def test_program_payload_reports_the_cap(client):
    assert client.get("/api/programs/p1").json()["max_proposed"] == 0


def test_setting_the_cap_is_reflected_in_the_payload(client):
    r = client.post("/api/programs/p1/max_proposed", json={"n": 6})
    assert r.status_code == 200
    assert r.json() == {"id": "p1", "max_proposed": 6}
    assert client.get("/api/programs/p1").json()["max_proposed"] == 6


def test_zero_clears_the_override(client):
    client.post("/api/programs/p1/max_proposed", json={"n": 6})
    r = client.post("/api/programs/p1/max_proposed", json={"n": 0})
    assert r.status_code == 200 and r.json()["max_proposed"] == 0


@pytest.mark.parametrize("n", [-1, 21])
def test_out_of_range_is_rejected(client, n):
    assert client.post("/api/programs/p1/max_proposed", json={"n": n}).status_code == 422


def test_unknown_program_is_404(client):
    assert client.post("/api/programs/nope/max_proposed", json={"n": 2}).status_code == 404
