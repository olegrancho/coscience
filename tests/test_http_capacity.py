import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    return TestClient(build_app(Service(tmp_path)))


def test_put_capacity_updates_and_returns_ledger_status(client):
    r = client.put("/api/capacity", json={"capacity": {"cpu": 16, "workers": 1}})
    assert r.status_code == 200
    assert r.json()["capacity"] == {"cpu": 16.0, "workers": 1.0}


def test_put_capacity_is_reflected_by_get_ledger(client):
    client.put("/api/capacity", json={"capacity": {"workers": 2}})
    assert client.get("/api/ledger").json()["capacity"] == {"workers": 2.0}


def test_put_capacity_rejects_a_negative_value(client):
    r = client.put("/api/capacity", json={"capacity": {"cpu": -1}})
    assert r.status_code == 422


def test_put_capacity_rejects_a_blank_name(client):
    r = client.put("/api/capacity", json={"capacity": {"": 1}})
    assert r.status_code == 422


def test_put_capacity_accepts_an_empty_pool(client):
    r = client.put("/api/capacity", json={"capacity": {}})
    assert r.status_code == 200
    assert r.json()["capacity"] == {}
