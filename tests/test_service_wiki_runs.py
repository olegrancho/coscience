import pytest

from coscience import wiki_store
from coscience.models import Result, Sprint, SprintStatus
from coscience.service import Service
from tests.test_wiki_beat import FakeWikiAgent


def _seed_object(substrate):
    substrate.save_sprint(Sprint(id="s0", status=SprintStatus.DONE, goals="g", program="p1"))
    substrate.save_result(Result(id="r0", sprint="s0", summary="s", completed_at=1.0))


def test_run_wiki_launches_an_ingest_through_the_beat(wiki_bundle):
    substrate, _ = wiki_bundle
    _seed_object(substrate)
    agent = FakeWikiAgent()
    out = Service(substrate.repo_root).run_wiki("p1", "ingest", agent=agent)
    assert "launched" in out["line"]
    assert len(agent.launches) == 1
    assert wiki_store.load_state(substrate, "p1")["run"] is not None


def test_run_wiki_never_starts_a_second_run(wiki_bundle):
    substrate, _ = wiki_bundle
    _seed_object(substrate)
    svc, agent = Service(substrate.repo_root), FakeWikiAgent()
    svc.run_wiki("p1", "ingest", agent=agent)
    out = svc.run_wiki("p1", "ingest", agent=agent)
    assert out["line"] == "wiki: running"
    assert len(agent.launches) == 1


def test_run_wiki_rejects_an_unknown_kind(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(ValueError):
        Service(substrate.repo_root).run_wiki("p1", "reticulate", agent=FakeWikiAgent())


def test_unquarantine_clears_the_list_and_says_what_it_cleared(wiki_bundle):
    substrate, _ = wiki_bundle
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = ["result:r9", "result:r8"]
    out = Service(substrate.repo_root).unquarantine_wiki("p1")
    assert sorted(out["cleared"]) == ["result:r8", "result:r9"]
    assert wiki_store.load_state(substrate, "p1")["quarantined"] == []


def test_unquarantine_with_nothing_quarantined_is_a_no_op(wiki_bundle):
    substrate, _ = wiki_bundle
    assert Service(substrate.repo_root).unquarantine_wiki("p1")["cleared"] == []
