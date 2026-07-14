import pytest
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus


def _svc(tmp_path, seed=True):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    if seed:
        d = tmp_path / ".coscience"; d.mkdir(parents=True, exist_ok=True)
        (d / "users.yaml").write_text(
            "users:\n  - username: stroganov\n    name: Oleg Stroganov\n    initials: OS\n")
    return svc


def test_gate_blocks_when_seeded(tmp_path):
    c = TestClient(build_app(_svc(tmp_path)))
    assert c.get("/api/health").status_code == 200          # open
    assert c.get("/api/sprints").status_code == 401         # gated
    # /api/me is a soft endpoint: 200 with required=true, user=null when logged out
    me = c.get("/api/me")
    assert me.status_code == 200 and me.json() == {"user": None, "required": True}


def test_login_me_logout(tmp_path):
    c = TestClient(build_app(_svc(tmp_path)))
    assert c.post("/api/login", json={"username": "ghost"}).status_code == 401
    r = c.post("/api/login", json={"username": "stroganov"})
    assert r.status_code == 200 and r.json()["initials"] == "OS"
    assert c.get("/api/sprints").status_code == 200         # cookie now carried
    assert c.get("/api/me").json()["user"]["username"] == "stroganov"
    c.post("/api/logout")
    assert c.get("/api/sprints").status_code == 401


def test_auth_disabled_when_no_registry(tmp_path):
    c = TestClient(build_app(_svc(tmp_path, seed=False)))
    assert c.get("/api/sprints").status_code == 200         # open
    assert c.get("/api/me").json() == {"user": None, "required": False}
    assert c.get("/api/users").json() == []
