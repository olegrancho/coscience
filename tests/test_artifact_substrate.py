from coscience.models import Artifact, ArtifactVersion


def test_save_load_roundtrip(substrate):
    a = Artifact(id="manuscript", program="kg-biomed", title="Manuscript",
                 kind="md", current="v1",
                 lock={"holder_kind": "chat", "holder_id": "chat:ab12",
                       "acquired_at": 10.0, "last_activity": 20.0},
                 versions=[ArtifactVersion(id="v1", created_by="human",
                                           created_at=5.0, note="first")],
                 threads=[{"id": "t1"}])
    substrate.save_artifact(a)
    b = substrate.load_artifact("kg-biomed", "manuscript")
    assert b.title == "Manuscript"
    assert b.kind == "md"
    assert b.current == "v1"
    assert b.lock["holder_id"] == "chat:ab12"
    assert b.lock["last_activity"] == 20.0
    assert len(b.versions) == 1
    assert b.versions[0].created_by == "human"
    assert b.versions[0].note == "first"
    assert b.threads == [{"id": "t1"}]
    assert b.archived is False


def test_artifact_dir_path(substrate):
    p = substrate.artifact_dir("kg-biomed", "fig")
    assert p == substrate.program_dir("kg-biomed") / "artifacts" / "fig"


def test_iter_artifacts_hides_archived_by_default(substrate):
    substrate.save_artifact(Artifact(id="a1", program="p", title="A1"))
    substrate.save_artifact(Artifact(id="a2", program="p", title="A2", archived=True))
    ids = [a.id for a in substrate.iter_artifacts("p")]
    assert ids == ["a1"]
    ids_all = [a.id for a in substrate.iter_artifacts("p", include_archived=True)]
    assert ids_all == ["a1", "a2"]


def test_iter_artifacts_empty_when_none(substrate):
    assert substrate.iter_artifacts("no-such-program") == []
