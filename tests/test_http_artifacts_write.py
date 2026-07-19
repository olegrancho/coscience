from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.http_api import build_app
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _two(substrate, aid="doc"):
    artifacts.create_artifact(substrate, "p", aid, aid, "md")
    for text, now in (("one", 1.0), ("two", 2.0)):
        work = artifacts.seed_work(substrate, "p", aid)
        (work / "c.md").write_text(text)
        artifacts.cut_version(substrate, "p", aid, "human", now=now)


def test_revert(substrate):
    _two(substrate)
    c = _client(substrate)
    r = c.post("/api/programs/p/artifacts/doc/revert", json={"vid": "v1"})
    assert r.status_code == 200
    assert r.json()["current"] == "v1"


def test_revert_unknown_422(substrate):
    _two(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p/artifacts/doc/revert", json={"vid": "v9"}).status_code == 422


def test_archive_artifact_and_version(substrate):
    _two(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p/artifacts/doc/archive", json={"archived": True}).json()["archived"] is True
    r = c.post("/api/programs/p/artifacts/doc/versions/v1/archive", json={"archived": True})
    assert next(v for v in r.json()["versions"] if v["id"] == "v1")["archived"] is True


def test_comment_and_thread_lifecycle(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "doc", "md")
    c = _client(substrate)
    t = c.post("/api/programs/p/artifacts/doc/comments", json={"text": "tighten intro"}).json()
    assert t["target"] == "pm"
    tid = t["id"]
    assert c.post(f"/api/programs/p/artifacts/doc/threads/{tid}/complete").json()["status"] == "complete"
    assert c.delete(f"/api/programs/p/artifacts/doc/threads/{tid}").status_code == 204
    assert c.get("/api/programs/p/artifacts/doc").json()["threads"] == []
