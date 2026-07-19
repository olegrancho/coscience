from coscience.service import Service


def test_submit_sprint_persists_artifact_fields(substrate):
    svc = Service(substrate.repo_root)
    svc.submit_sprint(id="s1", goals="g", plan=["do"], program="p",
                      artifacts_bound=["manuscript"],
                      artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    s = substrate.load_sprint("s1")
    assert s.artifacts_bound == ["manuscript"]
    assert s.artifacts_create == [{"aid": "fig", "title": "Fig", "kind": "figure"}]


def test_submit_sprint_defaults_empty(substrate):
    svc = Service(substrate.repo_root)
    svc.submit_sprint(id="s2", goals="g", plan=["do"], program="p")
    s = substrate.load_sprint("s2")
    assert s.artifacts_bound == []
    assert s.artifacts_create == []
