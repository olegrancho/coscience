from coscience import artifacts
from coscience.models import Sprint, SprintStatus
from coscience.service import NotFoundError, Service


def _seed_artifact(substrate, aid="doc", kind="md", text="hello"):
    artifacts.create_artifact(substrate, "p", aid, aid.title(), kind)
    work = artifacts.seed_work(substrate, "p", aid)
    (work / "content.md").write_text(text)
    artifacts.cut_version(substrate, "p", aid, "human", now=1.0)


def test_list_artifacts_hides_archived(substrate):
    _seed_artifact(substrate, "a")
    _seed_artifact(substrate, "b")
    artifacts.archive_artifact(substrate, "p", "b")
    svc = Service(substrate.repo_root)
    ids = [a["id"] for a in svc.list_artifacts("p")]
    assert ids == ["a"]
    a = next(x for x in svc.list_artifacts("p") if x["id"] == "a")
    assert a["kind"] == "md"
    assert a["current"] == "v1"
    assert a["version_count"] == 1


def test_get_artifact_returns_tree_and_files(substrate):
    _seed_artifact(substrate, "doc", text="one")
    work = artifacts.seed_work(substrate, "p", "doc")
    (work / "content.md").write_text("two")
    artifacts.cut_version(substrate, "p", "doc", "human", now=2.0)      # v2
    svc = Service(substrate.repo_root)
    d = svc.get_artifact("p", "doc")
    assert d["current"] == "v2"
    assert [v["id"] for v in d["versions"]] == ["v1", "v2"]
    assert d["versions"][1]["parent"] == "v1"
    assert d["current_files"] == ["content.md"]
    assert d["threads"] == []


def test_get_artifact_missing_raises(substrate):
    svc = Service(substrate.repo_root)
    try:
        svc.get_artifact("p", "nope")
        assert False, "expected NotFoundError"
    except NotFoundError:
        pass


def test_linked_sprints_cross_reference(substrate):
    _seed_artifact(substrate, "doc")
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g",
                                 plan=["x"], program="p", artifacts_bound=["doc"]))
    svc = Service(substrate.repo_root)
    d = svc.get_artifact("p", "doc")
    assert [s["id"] for s in d["linked_sprints"]] == ["s1"]
    assert d["linked_sprints"][0]["status"] == "queued"
