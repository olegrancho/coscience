from fastapi import FastAPI

import coscience.http_api as http_api
from coscience.service import Service, service_from_env


def test_service_from_env_uses_repo_var(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    svc = service_from_env()
    assert isinstance(svc, Service)
    assert svc.repo_root == tmp_path


def test_service_from_env_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_REPO", raising=False)
    monkeypatch.chdir(tmp_path)
    assert service_from_env().repo_root == tmp_path


def test_create_app_builds_fastapi(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    assert isinstance(http_api.create_app(), FastAPI)


def test_main_runs_uvicorn(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_PORT", "9999")
    captured = {}

    def fake_run(app, host, port):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(http_api.uvicorn, "run", fake_run)
    http_api.main()
    assert captured["port"] == 9999
    assert captured["host"] == "0.0.0.0"


def _ui(tmp_path):
    """A minimal built frontend: index.html plus one hashed asset."""
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text(
        '<script type="module" src="/assets/index-AAAA.js"></script>')
    (ui / "assets" / "index-AAAA.js").write_text("console.log(1)")
    return ui


def test_spa_entry_document_is_never_cached(tmp_path, monkeypatch):
    """The failure this prevents: `npm run build` empties dist, so every
    previously-hashed bundle is deleted on deploy. A browser holding a cached
    index.html then requests a script that no longer exists — a blank page — or
    keeps running the JS it already has and silently shows the old build. With
    no Cache-Control at all, heuristic caching made both reachable."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_UI_DIR", str(_ui(tmp_path)))
    client = TestClient(http_api.create_app())

    for route in ("/", "/programs/p1/wiki", "/programs/p1/wiki/graph"):
        r = client.get(route)
        assert r.status_code == 200, route
        assert "no-store" in r.headers.get("cache-control", ""), route


def test_hashed_assets_are_cacheable_forever(tmp_path, monkeypatch):
    """The other half: asset URLs carry a content hash, so their bytes never
    change and re-fetching them on every navigation would be waste."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_UI_DIR", str(_ui(tmp_path)))
    client = TestClient(http_api.create_app())

    r = client.get("/assets/index-AAAA.js")
    assert r.status_code == 200
    assert "immutable" in r.headers.get("cache-control", "")
