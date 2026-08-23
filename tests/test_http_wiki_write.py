from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_store
from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A",
        body="# Definition\n\n" + "x" * 400))


def test_verify_marks_the_page_and_derives_the_tier(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/pages/concepts/a/verify")
    assert r.status_code == 200
    assert r.json()["trust"] == "human-reviewed"


def test_the_verify_actor_is_built_server_side_and_is_always_a_human_prefix(substrate):
    _seed(substrate)
    body = _client(substrate).post(
        "/api/programs/p1/wiki/pages/concepts/a/verify").json()
    assert body["verified"][-1]["by"].startswith("human:")


def test_status_accepts_a_lifecycle_value_and_rejects_others(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p1/wiki/status/concepts/a",
                  json={"status": "stable"}).json()["status"] == "stable"
    assert c.post("/api/programs/p1/wiki/status/concepts/a",
                  json={"status": "verified"}).status_code == 400


def test_notes_write_the_protected_section(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/notes/concepts/a",
                                json={"text": "mind the lease id"})
    assert r.json()["human_notes"] == "mind the lease id"


def test_delete_removes_the_page(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.delete("/api/programs/p1/wiki/pages/concepts/a").status_code == 200
    assert c.get("/api/programs/p1/wiki/pages/concepts/a").status_code == 404


def test_unquarantine_reports_what_it_cleared(substrate):
    _seed(substrate)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = ["result:r9"]
    r = _client(substrate).post("/api/programs/p1/wiki/unquarantine")
    assert r.json()["cleared"] == ["result:r9"]


def test_run_rejects_an_unknown_kind_with_400(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/run", json={"kind": "nope"})
    assert r.status_code == 400


def test_curating_a_missing_page_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).post(
        "/api/programs/p1/wiki/pages/concepts/ghost/verify").status_code == 404
