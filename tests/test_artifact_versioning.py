from coscience import artifacts
from coscience.models import Artifact


def _write(work, name, text):
    (work / name).write_text(text)


def test_create_artifact(substrate):
    a = artifacts.create_artifact(substrate, "p", "fig", "Figure", "figure")
    assert a.kind == "figure"
    assert a.current == ""
    assert (substrate.artifact_dir("p", "fig") / "meta.md").is_file()


def test_create_rejects_duplicate(substrate):
    artifacts.create_artifact(substrate, "p", "fig", "Figure", "figure")
    try:
        artifacts.create_artifact(substrate, "p", "fig", "Figure", "figure")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_first_version_from_work(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    work = artifacts.seed_work(substrate, "p", "doc")
    _write(work, "content.md", "hello")
    vid = artifacts.cut_version(substrate, "p", "doc", "human", now=1.0, note="first")
    assert vid == "v1"
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert a.versions[0].parent == ""
    assert a.versions[0].note == "first"
    assert (substrate.artifact_dir("p", "doc") / "v1" / "content.md").read_text() == "hello"


def test_second_version_branches_from_current(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    work = artifacts.seed_work(substrate, "p", "doc")
    _write(work, "content.md", "one")
    artifacts.cut_version(substrate, "p", "doc", "human", now=1.0)
    work = artifacts.seed_work(substrate, "p", "doc")           # reseeded from v1
    assert (work / "content.md").read_text() == "one"           # seed copies current
    _write(work, "content.md", "two")
    vid = artifacts.cut_version(substrate, "p", "doc", "chat:x", now=2.0)
    assert vid == "v2"
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v2"
    assert a.versions[1].parent == "v1"


def test_dedup_identical_work_cuts_nothing(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    work = artifacts.seed_work(substrate, "p", "doc")
    _write(work, "content.md", "same")
    artifacts.cut_version(substrate, "p", "doc", "human", now=1.0)   # v1
    artifacts.seed_work(substrate, "p", "doc")                       # identical to v1
    vid = artifacts.cut_version(substrate, "p", "doc", "human", now=2.0)
    assert vid is None
    a = substrate.load_artifact("p", "doc")
    assert [v.id for v in a.versions] == ["v1"]
    assert a.current == "v1"


def test_cut_without_work_returns_none(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    assert artifacts.cut_version(substrate, "p", "doc", "human", now=1.0) is None
