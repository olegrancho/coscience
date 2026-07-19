from coscience.models import Artifact, ArtifactVersion


def test_artifact_defaults():
    a = Artifact(id="manuscript", program="kg-biomed")
    assert a.kind == "md"
    assert a.current == ""
    assert a.lock == {}
    assert a.versions == []
    assert a.threads == []
    assert a.archived is False


def test_artifact_version_defaults():
    v = ArtifactVersion(id="v1")
    assert v.parent == ""
    assert v.created_by == ""
    assert v.archived is False
    assert v.note == ""


def test_artifact_holds_versions():
    a = Artifact(id="fig", program="p", kind="figure", current="v2",
                 versions=[ArtifactVersion(id="v1"),
                           ArtifactVersion(id="v2", parent="v1")])
    assert a.current == "v2"
    assert [v.id for v in a.versions] == ["v1", "v2"]
    assert a.versions[1].parent == "v1"
