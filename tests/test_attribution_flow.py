from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus


def _client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g",
                                     plan=["a"], program="p1"))
    d = tmp_path / ".coscience"; d.mkdir(parents=True, exist_ok=True)
    (d / "users.yaml").write_text("users:\n  - username: stroganov\n    name: Oleg Stroganov\n")
    c = TestClient(build_app(svc))
    c.post("/api/login", json={"username": "stroganov"})
    return c, svc


def test_approve_records_actor(tmp_path):
    c, svc = _client(tmp_path)
    assert c.post("/api/sprints/s1/approve").status_code == 200
    decisions = svc.substrate.load_sprint("s1").decisions
    assert decisions[-1]["by"] == "stroganov" and decisions[-1]["action"] == "approve"


def test_comment_actor_is_server_derived_not_client(tmp_path):
    c, svc = _client(tmp_path)
    # client tries to spoof a different author in the body — must be ignored
    r = c.post("/api/sprints/s1/comments", json={"text": "hi", "target": "pm", "by": "apathak"})
    assert r.status_code == 201 and r.json()["by"] == "stroganov"


def test_vote_uses_username_when_authed(tmp_path):
    c, svc = _client(tmp_path)
    c.post("/api/sprints/s1/vote", json={"by": "browser-xyz", "value": 1})
    votes = svc.substrate.load_sprint("s1").votes
    assert votes[0]["by"] == "stroganov"   # server identity, not the body's browser id
