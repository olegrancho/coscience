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


def test_hand_written_metadata_loads_instead_of_raising(substrate):
    """An agent that writes an artifact's meta.md itself leaves fields the platform
    would have filled. One `created_at: null` from a real sprint stopped the PM and
    dispatch loops on every beat for seventeen hours: the dispatch cycle loads every
    artifact of every program, and float(None) raised inside load_artifact."""
    d = substrate.artifact_dir("kg-biomed", "figures")
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.md").write_text(
        "---\n"
        "type: artifact\n"
        "title: Manuscript figures\n"
        "kind: figure\n"
        "current: v1\n"
        "lock: {}\n"
        "versions:\n"
        "- id: v1\n"
        "  parent: ''\n"
        "  created_at: null\n"
        "  created_by: sprint:kg-biomed-c0-manuscript-figures\n"
        "  archived: false\n"
        "  note: two consolidated figures\n"
        "---\n")
    a = substrate.load_artifact("kg-biomed", "figures")
    assert a.current == "v1"
    assert a.versions[0].created_at == 0.0
    assert a.versions[0].created_by == "sprint:kg-biomed-c0-manuscript-figures"
    # ...and the whole-program read the loops do must survive it too.
    assert [x.id for x in substrate.iter_artifacts("kg-biomed")] == ["figures"]


def test_unparseable_timestamp_does_not_raise_either(substrate):
    d = substrate.artifact_dir("p", "a")
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.md").write_text(
        "---\ntype: artifact\ntitle: A\nkind: md\ncurrent: v1\nlock: {}\n"
        "versions:\n- id: v1\n  created_at: yesterday\n---\n")
    assert substrate.load_artifact("p", "a").versions[0].created_at == 0.0


def test_null_timestamps_survive_outside_artifacts_too(substrate):
    """The same `float(x.get(k, 0.0))` shape covered every timestamp this module
    reads. One malformed file must not be able to stop a loop, whichever it is."""
    import re

    from coscience.models import Sprint, SprintStatus
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g"))
    p = substrate.sprint_dir("s1") / "sprint.md"
    # Null out every timestamp the file carries, the nullable and the not.
    p.write_text(re.sub(r"(created_at|at): [0-9.]+", r"\g<1>: null", p.read_text()))

    s = substrate.load_sprint("s1")
    assert s.created_at is None                      # nullable: stays unknown
    assert s.status_history[0]["at"] == 0.0           # not nullable: floors to 0
