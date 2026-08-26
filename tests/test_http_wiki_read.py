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
        path="concepts/lease.md", type="Concept", title="Compute lease",
        body="# Definition\n\n" + "x" * 400))


def test_summary_lists_counts(substrate):
    _seed(substrate)
    r = _client(substrate).get("/api/programs/p1/wiki")
    assert r.status_code == 200
    assert r.json()["counts"] == {"Concept": 1}


def test_pages_and_page_detail(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert [p["path"] for p in c.get("/api/programs/p1/wiki/pages").json()] == \
        ["concepts/lease.md"]
    r = c.get("/api/programs/p1/wiki/pages/concepts/lease")
    assert r.status_code == 200
    assert r.json()["title"] == "Compute lease"


def test_a_nested_slug_survives_the_path_converter(substrate):
    # Without {slug:path} the slash in "concepts/lease" never matches the route.
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/pages/concepts/lease").status_code == 200


def test_a_missing_page_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/pages/concepts/nope").status_code == 404


def test_a_traversing_slug_is_404_not_a_file_read(substrate):
    _seed(substrate)
    r = _client(substrate).get("/api/programs/p1/wiki/pages/../../../etc/passwd")
    assert r.status_code == 404
    assert "root:" not in r.text


def test_search_log_and_lint(substrate):
    _seed(substrate)
    c = _client(substrate)
    hits = c.get("/api/programs/p1/wiki/search", params={"q": "compute"}).json()
    assert [h["path"] for h in hits] == ["concepts/lease.md"]
    assert c.get("/api/programs/p1/wiki/log").status_code == 200
    assert "counts" in c.get("/api/programs/p1/wiki/lint").json()


def test_a_blank_search_returns_an_empty_list(substrate):
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/search", params={"q": ""}).json() == []


def test_the_summary_endpoint_serves_the_program_wiki_model(substrate):
    substrate.save_program(Program(id="p1", title="P1", goals="g",
                                   wiki_model="claude-opus-5"))
    wiki_store.ensure_bundle(substrate, "p1")
    body = _client(substrate).get("/api/programs/p1/wiki").json()
    assert body["wiki_model"] == "claude-opus-5"
    assert body["wiki_enabled"] is True


def test_changing_the_wiki_model_shows_up_in_the_next_summary(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.get("/api/programs/p1/wiki").json()["wiki_model"] != "claude-opus-5"
    c.post("/api/programs/p1/wiki-model", json={"model": "claude-opus-5"})
    assert c.get("/api/programs/p1/wiki").json()["wiki_model"] == "claude-opus-5"
