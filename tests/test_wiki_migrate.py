"""A wiki moves to the current layout by a sequence of beat-driven runs, and the
old wiki serves until the swap (docs/wiki-layouts.md)."""
import json

import pytest

from coscience import wiki, wiki_layouts, wiki_migrate, wiki_okf, wiki_store
from tests.test_wiki_beat import FakeWikiAgent
from tests.test_wiki_beat_collect import _seed


def _legacy_wiki(substrate):
    """A program whose wiki is in the older layout, with one page and one source."""
    p = _seed(substrate)
    bundle = wiki_store.bundle_dir(substrate, "p1")
    (bundle / "sources").mkdir(parents=True)
    (bundle / "CLAUDE.md").write_text(wiki_store.BUNDLE_CLAUDE_MD)
    (bundle / "log.md").write_text("# Log\n\nNewest first.\n\n- 2026-09-01 — an ingest\n")
    (bundle / "sources" / "result-r0.md").write_text("---\ntype: Source\n---\nsee [a](/concepts/a.md)\n")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x" * 400))
    return p


def _finish(agent, substrate, p, now, write=None):
    run_dir = agent.launches[-1]["run_dir"]
    if write:
        write()
    (run_dir / "agent.exit").write_text("0\n")
    agent.alive = False
    line = wiki.beat(substrate, p, now, agent)
    agent.alive = True
    return line


def _map(substrate, n_topics=7):
    topics = [{"slug": f"concepts/t{i}.md", "title": f"T{i}", "from": ["concepts/a.md"]}
              for i in range(n_topics)]
    wiki_migrate.map_path(substrate, "p1").write_text(json.dumps(
        {"topics": topics, "background": [{"slug": "entities/b.md", "title": "B", "from": []}],
         "syntheses": [], "terms": []}))


def test_a_new_or_current_wiki_has_nothing_to_migrate(substrate):
    _seed(substrate)
    with pytest.raises(ValueError):
        wiki_migrate.request(substrate, "p1", by="t", now=1.0)


def test_a_migration_runs_map_then_batches_then_finish_then_swaps(substrate):
    agent = FakeWikiAgent()
    p = _legacy_wiki(substrate)
    assert wiki_migrate.available(substrate, "p1") == wiki_layouts.CURRENT
    wiki_migrate.request(substrate, "p1", by="human:t", now=1.0)

    # setup + map: the staging bundle has the sources and the current schema
    line = wiki.beat(substrate, p, 100.0, agent)
    assert line.startswith("wiki: launched migrate map")
    stage = wiki_migrate.staging_bundle(substrate, "p1")
    assert agent.launches[-1]["kind"] == "migrate" and agent.launches[-1]["run_dir"]
    assert (stage / "sources" / "result-r0.md").is_file()
    assert wiki_layouts.read(stage) == wiki_layouts.CURRENT
    assert "topic map" in agent.launches[-1]["report"]

    assert _finish(agent, substrate, p, 110.0, lambda: _map(substrate)) == \
        "wiki: migrate map ok — 3 batches to write"      # 7 topics -> 5+2, 1 background

    for i, t in enumerate((200.0, 300.0, 400.0)):
        assert "launched migrate write" in wiki.beat(substrate, p, t, agent)
        assert "concepts/t" in agent.launches[-1]["report"] or "entities/b" in agent.launches[-1]["report"]
        _finish(agent, substrate, p, t + 10)

    assert "launched migrate finish" in wiki.beat(substrate, p, 500.0, agent)
    line = _finish(agent, substrate, p, 510.0,
                   lambda: (stage / "index.md").write_text("# new index\n"))
    assert line.startswith("wiki: migrated to topics v1")

    live = wiki_store.bundle_dir(substrate, "p1")
    assert wiki_layouts.read(live) == wiki_layouts.CURRENT
    assert (live / "index.md").read_text() == "# new index\n"
    assert not (live / ".topic-map.json").exists()
    assert "migrated from concepts v1 to topics v1" in (live / "log.md").read_text().split("- 2026-09-01")[0]
    state = wiki_store.load_state(substrate, "p1")
    assert "migration" not in state and state["sweep_requested"] is True
    archived = substrate.program_dir("p1") / state["layout_history"][0]["archived"]
    assert (archived / "concepts" / "a.md").is_file()          # the old wiki, kept
    assert not wiki_migrate.staging_dir(substrate, "p1").exists()


def test_nothing_else_runs_while_a_migration_is_under_way(substrate):
    agent = FakeWikiAgent()
    p = _legacy_wiki(substrate)        # r0 has a source file but is not in the ledger: pending
    wiki_migrate.request(substrate, "p1", by="t", now=1.0)
    wiki.beat(substrate, p, 100.0, agent)
    assert [l["kind"] for l in agent.launches] == ["migrate"]


def test_a_failing_step_is_retried_then_stops_with_the_old_wiki_live(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "2")
    agent = FakeWikiAgent()
    p = _legacy_wiki(substrate)
    wiki_migrate.request(substrate, "p1", by="t", now=1.0)
    for t in (100.0, 200.0):
        wiki.beat(substrate, p, t, agent)
        (agent.launches[-1]["run_dir"] / "agent.exit").write_text("1\n")
        agent.alive = False
        line = wiki.beat(substrate, p, t + 10, agent)
        agent.alive = True
    assert line.startswith("wiki: migration stopped")
    assert wiki.beat(substrate, p, 300.0, agent) == ""
    assert wiki_layouts.read(wiki_store.bundle_dir(substrate, "p1")) == wiki_layouts.CONCEPTS_V1
    # asking again resumes the failed phase
    assert wiki_migrate.request(substrate, "p1", by="t", now=2.0)["phase"] == "map"
    assert "launched migrate map" in wiki.beat(substrate, p, 400.0, agent)


def test_cancel_discards_staging_but_not_mid_run(substrate):
    agent = FakeWikiAgent()
    p = _legacy_wiki(substrate)
    wiki_migrate.request(substrate, "p1", by="t", now=1.0)
    wiki.beat(substrate, p, 100.0, agent)
    with pytest.raises(ValueError):
        wiki_migrate.cancel(substrate, "p1")
    _finish(agent, substrate, p, 110.0, lambda: _map(substrate))
    assert wiki_migrate.cancel(substrate, "p1") is True
    assert not wiki_migrate.staging_dir(substrate, "p1").exists()
    assert "migration" not in wiki_store.load_state(substrate, "p1")


def test_topic_counts_scale_with_the_wiki():
    assert wiki_migrate._target_topics(52) == 18
    assert wiki_migrate._target_topics(135) == 29
    assert wiki_migrate._target_topics(3) == 6


def test_the_summary_and_api_show_and_start_a_migration(substrate):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    from coscience.service import Service
    _legacy_wiki(substrate)
    c = TestClient(build_app(Service(substrate.repo_root)))
    s = c.get("/api/programs/p1/wiki").json()
    assert (s["layout"], s["layout_current"], s["layout_upgrade"], s["migration"]) == \
        ("concepts v1", "topics v1", "topics v1", None)
    r = c.post("/api/programs/p1/wiki/migrate")
    assert r.status_code == 200 and r.json()["to"] == "topics v1"
    assert r.json()["requested_by"] == "human:anonymous"
    assert c.get("/api/programs/p1/wiki").json()["migration"]["phase"] == "setup"
    assert c.delete("/api/programs/p1/wiki/migrate").json() == {"cancelled": True}


def test_the_api_refuses_a_migration_with_nothing_to_do(substrate):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    from coscience.service import Service
    _seed(substrate)
    c = TestClient(build_app(Service(substrate.repo_root)))
    assert c.post("/api/programs/p1/wiki/migrate").status_code == 400
