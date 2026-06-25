import coscience.mcp_server as mcp_server
from coscience.service import Service


def test_service_from_env_uses_repo_var(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    svc = mcp_server._service_from_env()
    assert isinstance(svc, Service)
    assert svc.repo_root == tmp_path


def test_service_from_env_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_REPO", raising=False)
    monkeypatch.chdir(tmp_path)
    assert mcp_server._service_from_env().repo_root == tmp_path


def test_main_builds_and_runs_server(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENCE_REPO", str(tmp_path))
    ran = {}

    def fake_run(self, *args, **kwargs):
        ran["yes"] = True

    monkeypatch.setattr("mcp.server.fastmcp.FastMCP.run", fake_run)
    mcp_server.main()
    assert ran.get("yes") is True
