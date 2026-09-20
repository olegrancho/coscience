from coscience import artifacts
from coscience.models import Sprint, SprintStatus
from coscience.service import Service


def _sprint(substrate, **kw):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g",
                                 plan=["x"], program="p", **kw))
    return Service(substrate.repo_root).get_sprint("s1")


def test_get_sprint_exposes_artifact_fields(substrate):
    d = _sprint(substrate, artifacts_bound=["doc"],
                artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    assert d["artifacts_bound"] == ["doc"]
    assert d["artifacts_create"] == [{"aid": "fig", "title": "Fig", "kind": "figure",
                                      "exists": False, "version": ""}]


def test_promised_artifact_that_was_never_made_stays_a_promise(substrate):
    d = _sprint(substrate, artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    assert d["artifacts_create"][0]["exists"] is False
    assert d["artifacts_create"][0]["version"] == ""


def test_promised_artifact_is_reported_once_it_exists(substrate):
    artifacts.create_artifact(substrate, "p", "fig", "Fig", "figure")
    d = _sprint(substrate, artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    assert d["artifacts_create"][0]["exists"] is True
    assert d["artifacts_create"][0]["version"] == ""      # instantiated, nothing cut yet


def test_promised_artifact_carries_its_current_version(substrate):
    artifacts.create_artifact(substrate, "p", "fig", "Fig", "figure")
    work = artifacts.seed_work(substrate, "p", "fig")
    (work / "fig.png").write_text("x")
    artifacts.cut_version(substrate, "p", "fig", "s1", now=1.0)
    d = _sprint(substrate, artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    assert d["artifacts_create"][0]["exists"] is True
    assert d["artifacts_create"][0]["version"] == "v1"


def test_unreadable_artifact_reads_as_a_promise_rather_than_500ing(substrate):
    artifacts.create_artifact(substrate, "p", "fig", "Fig", "figure")
    (substrate.artifact_dir("p", "fig") / "meta.md").write_text(
        "---\ntitle: [unclosed\n---\n\n# Fig\n")
    d = _sprint(substrate, artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    assert d["artifacts_create"][0]["exists"] is False


def test_create_spec_with_no_aid_is_left_alone(substrate):
    d = _sprint(substrate, artifacts_create=[{"title": "Fig", "kind": "figure"}])
    assert d["artifacts_create"][0]["exists"] is False
    assert d["artifacts_create"][0]["title"] == "Fig"
