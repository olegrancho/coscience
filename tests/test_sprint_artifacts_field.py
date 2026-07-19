from coscience.models import Sprint, SprintStatus
from coscience.service import Service


def test_get_sprint_exposes_artifact_fields(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g",
                                 plan=["x"], program="p",
                                 artifacts_bound=["doc"],
                                 artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}]))
    svc = Service(substrate.repo_root)
    d = svc.get_sprint("s1")
    assert d["artifacts_bound"] == ["doc"]
    assert d["artifacts_create"] == [{"aid": "fig", "title": "Fig", "kind": "figure"}]
