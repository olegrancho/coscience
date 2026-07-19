from coscience.models import Sprint, SprintStatus


def test_sprint_artifact_fields_default_empty():
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g")
    assert s.artifacts_bound == []
    assert s.artifacts_create == []


def test_sprint_artifact_fields_roundtrip(substrate):
    s = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["do"],
               program="prog",
               artifacts_bound=["manuscript", "umap"],
               artifacts_create=[{"aid": "table1", "title": "Table 1", "kind": "data"}])
    substrate.save_sprint(s)
    b = substrate.load_sprint("s1")
    assert b.artifacts_bound == ["manuscript", "umap"]
    assert b.artifacts_create == [{"aid": "table1", "title": "Table 1", "kind": "data"}]


def test_sprint_without_artifacts_omits_keys(substrate):
    substrate.save_sprint(Sprint(id="s2", status=SprintStatus.PROPOSED, goals="g", plan=["x"]))
    text = (substrate.sprint_dir("s2") / "sprint.md").read_text()
    assert "artifacts_bound" not in text
    assert "artifacts_create" not in text
