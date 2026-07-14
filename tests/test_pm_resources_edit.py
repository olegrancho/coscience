from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_pm_edit_sets_resources_on_queued(substrate):
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-a", status=SprintStatus.QUEUED, goals="g",
                                 plan=["a"], program="p1", resources_required={"gpu": 1}))
    out = PMCycleOutput(report="r", sprint_edits=[
        {"sprint_id": "p1-a", "resources_required": {"cpu": 2}}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    sp = substrate.load_sprint("p1-a")
    assert sp.resources_required == {"cpu": 2.0}
