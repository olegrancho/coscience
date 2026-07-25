"""HTTP surface for directory browsing: status-code mapping."""
import pytest
from fastapi.testclient import TestClient

from coscience import fs_browse
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


def test_outside_roots_403_does_not_disclose_a_resolved_path(client):
    """`?path=~root` must not expand `~` (that's request input, not config)
    and the 403 body must not echo any path back — neither `/root` from a
    tilde expansion nor anything else derived from resolving the input."""
    r = client.get("/api/fs/dirs", params={"path": "~root"})
    assert r.status_code == 403
    assert "/root" not in r.text
    assert "~root" not in r.text


def test_create_outside_roots_403_does_not_disclose_a_resolved_path(client, tmp_path):
    r = client.post("/api/fs/dirs", json={"parent": str(tmp_path), "name": "sneaky"})
    assert r.status_code == 403
    assert str(tmp_path) not in r.text


def test_create_long_name_is_400_not_500(client):
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "a" * 300})
    assert r.status_code == 400


def test_create_mkdir_oserror_is_4xx_not_500(client, monkeypatch):
    """A filesystem failure other than already-exists (EROFS, ENOSPC, ...)
    must map to a 4xx via fs_browse.CreateFailed, not escape as a 500."""
    def raise_create_failed(parent, name):
        raise fs_browse.CreateFailed("No space left on device")

    monkeypatch.setattr(fs_browse, "make_dir", raise_create_failed)
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "no-room"})
    assert 400 <= r.status_code < 500


def test_list_permission_denied_is_403(client, monkeypatch):
    def raise_permission_denied(path=None):
        raise PermissionError("denied")

    monkeypatch.setattr(fs_browse, "list_dirs", raise_permission_denied)
    r = client.get("/api/fs/dirs")
    assert r.status_code == 403


def test_create_permission_denied_is_403(client, monkeypatch):
    def raise_permission_denied(parent, name):
        raise PermissionError("denied")

    monkeypatch.setattr(fs_browse, "make_dir", raise_permission_denied)
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "x"})
    assert r.status_code == 403
