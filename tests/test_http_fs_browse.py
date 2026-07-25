"""HTTP surface for directory browsing: status-code mapping."""
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.service import Service


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(root))
    c = TestClient(build_app(Service(tmp_path)))   # same shape as tests/test_http_api.py
    c.root = root
    return c


def test_list_returns_subdirectories(client):
    (client.root / "child").mkdir()
    r = client.get("/api/fs/dirs", params={"path": str(client.root)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(client.root)
    assert body["parent"] is None
    assert body["roots"] == [{"label": str(client.root), "path": str(client.root)}]
    assert body["entries"] == [{"name": "child", "path": str(client.root / "child")}]


def test_list_without_path_opens_at_the_root(client):
    r = client.get("/api/fs/dirs")
    assert r.status_code == 200
    assert r.json()["path"] == str(client.root)


def test_list_outside_the_roots_is_403(client, tmp_path):
    r = client.get("/api/fs/dirs", params={"path": str(tmp_path)})
    assert r.status_code == 403


def test_list_missing_path_is_404(client):
    r = client.get("/api/fs/dirs", params={"path": str(client.root / "nope")})
    assert r.status_code == 404


def test_create_returns_201_and_the_path(client):
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "made"})
    assert r.status_code == 201
    assert r.json() == {"path": str(client.root / "made")}
    assert (client.root / "made").is_dir()


def test_create_invalid_name_is_400(client):
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "a/b"})
    assert r.status_code == 400


def test_create_duplicate_is_409(client):
    (client.root / "taken").mkdir()
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "taken"})
    assert r.status_code == 409


def test_create_outside_the_roots_is_403(client, tmp_path):
    r = client.post("/api/fs/dirs", json={"parent": str(tmp_path), "name": "sneaky"})
    assert r.status_code == 403


def test_list_null_byte_path_is_404_not_500(client):
    """A null byte makes Path.resolve() raise a raw ValueError; _checked() must
    convert it to fs_browse.NotFound so it maps to 404, not a raw 500."""
    r = client.get("/api/fs/dirs", params={"path": "a\0b"})
    assert r.status_code == 404
