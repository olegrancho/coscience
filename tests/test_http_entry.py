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
