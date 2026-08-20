import time

from coscience import artifacts, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus


def _program(substrate, pid="p1"):
    substrate.save_program(Program(id=pid, title=pid.upper(), goals="g"))


def _result(substrate, rid, sprint_id, program, at):
    substrate.save_sprint(Sprint(id=sprint_id, status=SprintStatus.DONE, goals="g",
                                 program=program))
    substrate.save_result(Result(id=rid, sprint=sprint_id, summary=f"summary {rid}",
                                 completed_at=at))


def test_program_objects_includes_results_of_this_program_only(substrate):
    _program(substrate, "p1")
    _program(substrate, "p2")
    _result(substrate, "r1", "s1", "p1", 100.0)
    _result(substrate, "r2", "s2", "p2", 200.0)
    oids = [o.oid for o in wiki_store.program_objects(substrate, "p1")]
    assert oids == ["result:r1"]


def test_result_with_missing_sprint_is_skipped(substrate):
    _program(substrate, "p1")
    substrate.save_result(Result(id="r9", sprint="gone", summary="s", completed_at=1.0))
    assert wiki_store.program_objects(substrate, "p1") == []


def test_objects_are_ordered_oldest_first(substrate):
    _program(substrate, "p1")
    _result(substrate, "late", "s2", "p1", 300.0)
    _result(substrate, "early", "s1", "p1", 100.0)
    assert [o.oid for o in wiki_store.program_objects(substrate, "p1")] == [
        "result:early", "result:late"]


def test_artifact_current_version_only(substrate, tmp_path):
    _program(substrate, "p1")
    src = tmp_path / "fig.md"
    src.write_text("v1\n")
    v1 = artifacts.adopt(substrate, "p1", "fig", title="Figure", kind="figure",
                         now=10.0, created_by="cli",
                         sources=artifacts.resolve_sources(tmp_path, [src], restrict=False))
    src.write_text("v2\n")
    v2 = artifacts.adopt(substrate, "p1", "fig", title="Figure", kind="figure",
                         now=20.0, created_by="cli",
                         sources=artifacts.resolve_sources(tmp_path, [src], restrict=False))
    assert v1 != v2
    oids = [o.oid for o in wiki_store.program_objects(substrate, "p1")]
    assert oids == [f"artifact:fig@{v2}"]


def test_slug_and_resource_are_platform_assigned(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    assert obj.slug == "sources/result-r1.md"
    assert obj.resource == "/results/r1.md"
    assert obj.paths == [substrate.repo_root / "results" / "r1.md"]


def test_object_hash_changes_when_the_file_changes(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    before = wiki_store.object_hash(obj)
    assert before.startswith("sha256:")
    (substrate.repo_root / "results" / "r1.md").write_text("edited\n")
    assert wiki_store.object_hash(obj) != before


def test_pending_excludes_ingested_but_returns_drifted(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    ingested = {obj.oid: {"hash": wiki_store.object_hash(obj), "at": 1.0, "run": "r1"}}
    assert wiki_store.pending_objects(substrate, "p1", ingested) == []
    # Edit through save_result (as a real edit would) so the sprint link in the
    # result's own frontmatter survives — Substrate.save_result stores "sprint"
    # in-band, so overwriting the raw file with plain text (no frontmatter) would
    # destroy that link and make the result unresolvable to any program, which is
    # exactly the "missing sprint" case test_result_with_missing_sprint_is_skipped
    # requires to be excluded — not what this test is after.
    substrate.save_result(Result(id="r1", sprint="s1", summary="edited", completed_at=100.0))
    assert [o.oid for o in wiki_store.pending_objects(substrate, "p1", ingested)] == ["result:r1"]


def test_pending_excludes_quarantined(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    assert wiki_store.pending_objects(substrate, "p1", {}, {"result:r1"}) == []
