from fastapi import FastAPI
from fastapi.testclient import TestClient

import coscience.http_api as http_api


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
