"""A sprint_edit can attach artifacts to an EDITABLE (still-proposed) sprint.

Regression: a human asked an already-proposed sprint to deliver its output as an
artifact; the PM could only rewrite goals prose (sprint_edits carried no artifact
fields), so the sprint ran with no binding and the worker wrote a plain file
instead of a versioned Artifact.
"""
from coscience import artifacts
from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_edit_creates_artifact_on_proposed_sprint(substrate):
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-a", status=SprintStatus.PROPOSED,
                                 goals="g", plan=["a"], program="p1"))
    out = PMCycleOutput(report="r", sprint_edits=[
        {"sprint_id": "p1-a",
         "artifacts_create": [{"title": "Program Report", "kind": "md"}]}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    sp = substrate.load_sprint("p1-a")
    assert sp.artifacts_create and sp.artifacts_create[0]["title"] == "Program Report"
    assert sp.artifacts_create[0]["kind"] == "md"
    assert sp.artifacts_create[0]["aid"] == "program-report"   # slug assigned
    # and the worker would now see it as a deliverable
    assert artifacts.sprint_aids(sp) == ["program-report"]


def test_edit_binds_existing_artifact_on_proposed_sprint(substrate):
    _prog(substrate)
    artifacts.create_artifact(substrate, "p1", "doc", "Doc", "md")
    substrate.save_sprint(Sprint(id="p1-b", status=SprintStatus.PROPOSED,
                                 goals="g", plan=["a"], program="p1"))
    out = PMCycleOutput(report="r", sprint_edits=[
        {"sprint_id": "p1-b", "artifacts_bound": ["doc"]}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    sp = substrate.load_sprint("p1-b")
    assert sp.artifacts_bound == ["doc"]


def test_edit_does_not_bind_artifacts_on_queued_sprint(substrate):
    """Deliverables are fixed before a sprint runs; a queued/approved sprint's
    artifact binding must not change (same gate as goals/plan)."""
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-c", status=SprintStatus.QUEUED,
                                 goals="g", plan=["a"], program="p1"))
    out = PMCycleOutput(report="r", sprint_edits=[
        {"sprint_id": "p1-c",
         "artifacts_create": [{"title": "Too Late", "kind": "md"}]}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    sp = substrate.load_sprint("p1-c")
    assert sp.artifacts_create == []
