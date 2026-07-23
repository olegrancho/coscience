from coscience import artifacts
from coscience.service import NotFoundError, Service


def _two_versions(substrate, aid="doc"):
    artifacts.create_artifact(substrate, "p", aid, aid, "md")
    for text, now in (("one", 1.0), ("two", 2.0)):
        work = artifacts.seed_work(substrate, "p", aid)
        (work / "c.md").write_text(text)
        artifacts.cut_version(substrate, "p", aid, "human", now=now)


def test_revert_artifact_moves_current(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    d = svc.revert_artifact("p", "doc", "v1")
    assert d["current"] == "v1"
    assert [v["id"] for v in d["versions"]] == ["v1", "v2"]   # nothing deleted


def test_revert_unknown_version_raises(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    try:
        svc.revert_artifact("p", "doc", "v9")
        assert False
    except ValueError:
        pass


def test_archive_whole_artifact(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    d = svc.set_artifact_archived("p", "doc", True)
    assert d["archived"] is True
    assert [a["id"] for a in svc.list_artifacts("p")] == []
    d = svc.set_artifact_archived("p", "doc", False)
    assert d["archived"] is False


def test_archive_single_version(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    d = svc.set_artifact_version_archived("p", "doc", "v1", True)
    v1 = next(v for v in d["versions"] if v["id"] == "v1")
    assert v1["archived"] is True
