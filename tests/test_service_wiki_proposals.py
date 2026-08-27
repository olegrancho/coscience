from __future__ import annotations

import pytest

from coscience import wiki_okf, wiki_store
from coscience.models import Program
from coscience.service import NotFoundError, Service


def _seed(substrate, proposals=True):
    substrate.save_program(Program(id="p1", title="P1", goals="g", wiki_merge="propose"))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title in (("concepts/a.md", "A"), ("concepts/b.md", "B")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title, body="# Definition\n\nx\n"))
    if proposals:
        with wiki_store.state_guard(substrate, "p1") as state:
            state["merge_proposals"] = [
                {"id": "m0001", "winner": "concepts/a.md", "loser": "concepts/b.md",
                 "why": "same idea", "run": "r0001", "at": 1.0}]
    return Service(substrate.repo_root)


def test_proposals_are_listed_with_the_agents_reasoning(substrate):
    rows = _seed(substrate).list_wiki_merges("p1")
    assert rows[0]["id"] == "m0001" and rows[0]["why"] == "same idea"


def test_accepting_applies_the_merge_now(substrate):
    """Spec 9.1: the wiki is never left in a state where a human approved
    something and nothing happened."""
    svc = _seed(substrate)
    out = svc.accept_wiki_merge("p1", "m0001")
    assert out["applied"] is True
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is None
    assert svc.list_wiki_merges("p1") == []


def test_accepting_a_stale_proposal_drops_it_without_an_error(substrate):
    """A proposal about a wiki that no longer exists is not an error worth
    surfacing to a human who did nothing wrong."""
    svc = _seed(substrate)
    svc.wiki_page_path("p1", "concepts/b").unlink()
    out = svc.accept_wiki_merge("p1", "m0001")
    assert out["applied"] is False
    assert svc.list_wiki_merges("p1") == []


def test_accepting_an_unknown_id_is_not_found(substrate):
    with pytest.raises(NotFoundError):
        _seed(substrate).accept_wiki_merge("p1", "m9999")


def test_rejecting_remembers_the_pair(substrate):
    svc = _seed(substrate)
    svc.reject_wiki_merge("p1", "m0001")
    assert svc.list_wiki_merges("p1") == []
    refused = wiki_store.load_state(substrate, "p1")["merges_refused"]
    assert sorted(refused[0]) == ["concepts/a.md", "concepts/b.md"]
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is not None


def test_activity_is_the_recorded_runs(substrate):
    svc = _seed(substrate, proposals=False)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs"] = [{"id": "r0002", "kind": "lint", "status": "ok", "at": 2.0,
                          "merged": [["concepts/b.md", "concepts/a.md"]]}]
    assert svc.wiki_activity("p1")[0]["id"] == "r0002"


def test_the_policy_can_be_set_and_rejects_anything_else(substrate):
    svc = _seed(substrate, proposals=False)
    assert svc.set_program_wiki_merge("p1", "auto")["wiki_merge"] == "auto"
    assert substrate.load_program("p1").wiki_merge == "auto"
    with pytest.raises(ValueError):
        svc.set_program_wiki_merge("p1", "sometimes")


def test_setting_the_policy_on_a_missing_program_is_not_found(substrate):
    with pytest.raises(NotFoundError):
        _seed(substrate, proposals=False).set_program_wiki_merge("ghost", "auto")
