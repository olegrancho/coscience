from __future__ import annotations

from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_store
from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate, policy="propose"):
    substrate.save_program(Program(id="p1", title="P1", goals="g", wiki_merge=policy))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title in (("concepts/a.md", "A"), ("concepts/b.md", "B")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title, body="# Definition\n\nx\n"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merge_proposals"] = [
            {"id": "m0001", "winner": "concepts/a.md", "loser": "concepts/b.md",
             "why": "same idea", "run": "r0001", "at": 1.0}]
        state["runs"] = [{"id": "r0001", "kind": "lint", "status": "ok", "at": 1.0,
                          "merged": []}]


def test_proposals_are_listed(substrate):
    _seed(substrate)
    body = _client(substrate).get("/api/programs/p1/wiki/merges").json()
    assert body[0]["id"] == "m0001"


def test_accepting_applies_and_reports_it(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/merges/m0001/accept")
    assert r.status_code == 200 and r.json()["applied"] is True


def test_accepting_an_unknown_proposal_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).post(
        "/api/programs/p1/wiki/merges/m9999/accept").status_code == 404


def test_rejecting_reports_the_pair(substrate):
    _seed(substrate)
    body = _client(substrate).post("/api/programs/p1/wiki/merges/m0001/reject").json()
    assert sorted(body["rejected"]) == ["concepts/a.md", "concepts/b.md"]


def test_activity_is_served(substrate):
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/activity").json()[0]["id"] == "r0001"


def test_the_policy_can_be_set_and_a_bad_one_is_400(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p1/wiki-merge-policy",
                  json={"policy": "auto"}).json()["wiki_merge"] == "auto"
    assert c.post("/api/programs/p1/wiki-merge-policy",
                  json={"policy": "nope"}).status_code == 400


def test_the_policy_on_a_missing_program_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).post("/api/programs/ghost/wiki-merge-policy",
                                   json={"policy": "auto"}).status_code == 404


def test_the_lint_route_carries_the_filed_reports(substrate):
    _seed(substrate)
    assert "reports" in _client(substrate).get("/api/programs/p1/wiki/lint").json()


def test_the_literal_merge_and_activity_routes_resolve(substrate):
    """Not a catch-all-swallowing test: /wiki/merges and /wiki/activity never
    share the wiki/pages/ prefix with {slug:path}, so that catch-all can't reach
    them regardless of registration order. This just pins that both literal
    routes resolve to their handlers (a 200 with a list), the way the docstring
    used to (incorrectly) claim was at risk."""
    _seed(substrate)
    assert isinstance(_client(substrate).get("/api/programs/p1/wiki/merges").json(), list)
    assert isinstance(_client(substrate).get("/api/programs/p1/wiki/activity").json(), list)


def test_accepting_a_stale_proposal_reports_it_without_erroring(substrate):
    """Spec 9.1: a proposal is re-checked at apply time. If a page moved on
    between an agent proposing and a human clicking, the apply is refused and
    the proposal dropped -- that is a fact about the wiki, not a client error,
    so it must reach the caller as 200 with applied: false, never a 4xx."""
    _seed(substrate)
    Service(substrate.repo_root).delete_wiki_page("p1", "concepts/b")
    r = _client(substrate).post("/api/programs/p1/wiki/merges/m0001/accept")
    assert r.status_code == 200
    assert r.json()["applied"] is False
