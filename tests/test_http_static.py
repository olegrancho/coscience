from fastapi import FastAPI
from fastapi.testclient import TestClient

import coscience.http_api as http_api

# ---------------------------------------------------------------------------
# I-1 path-traversal regression test
# ---------------------------------------------------------------------------


def test_spa_path_traversal_blocked(tmp_path, monkeypatch):
    """SPA catch-all must not serve files outside the bundle dir.

    Attack vector: httpx preserves percent-encoded dots (%2e%2e) without
    normalising '/..' path segments, so '/%2e%2e/secret.txt' reaches the
    spa() handler as full_path='../secret.txt'.  Python's pathlib.is_file()
    resolves '..' at OS level, so the unguarded code would return the secret.
    The fix resolves both paths and asserts containment before serving.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>spa-index</title>")

    # Secret file OUTSIDE the bundle dir — must never be served
    secret = tmp_path / "secret.txt"
    secret.write_text("SENTINEL_SECRET_VALUE")

    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_UI_DIR", str(dist))

    app = http_api.create_app()
    # raise_server_exceptions=False so a 500 shows as HTTP instead of crashing
    client = TestClient(app, raise_server_exceptions=False)

    # Traversal attempt: %2e%2e decodes to '..' but httpx does NOT collapse it,
    # so Starlette sees scope["path"] = "/../secret.txt" and full_path = "../secret.txt".
    resp = client.get("/%2e%2e/secret.txt")

    assert "SENTINEL_SECRET_VALUE" not in resp.text, (
        "Path traversal: SPA catch-all served the secret file outside ui_dir"
    )
    # The response must be the SPA index (200) or a rejection (404/400/403),
    # never the raw secret.
    assert resp.status_code in (200, 404, 400, 403), (
        f"Unexpected status {resp.status_code} for traversal path"
    )

    # Normal asset WITHIN the bundle is still served correctly.
    (dist / "bundle.js").write_text("console.log('app');")
    app2 = http_api.create_app()
    client2 = TestClient(app2, raise_server_exceptions=False)
    assert client2.get("/bundle.js").status_code == 200
    assert "console.log" in client2.get("/bundle.js").text


def test_build_app_is_api_only(tmp_path):
    # No SPA mount in build_app: an unknown non-/api path is a plain 404.
    from coscience.service import Service
    client = TestClient(http_api.build_app(Service(tmp_path)))
    assert client.get("/").status_code == 404


def test_create_app_serves_spa_when_bundle_present(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>ui</title>")
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_UI_DIR", str(dist))
    app = http_api.create_app()
    assert isinstance(app, FastAPI)
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert "ui" in client.get("/").text
    # client-side route falls back to index.html
    assert client.get("/programs/p1").status_code == 200
    # API still works under /api
    assert client.get("/api/health").json() == {"status": "ok"}
