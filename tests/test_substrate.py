from coscience.models import Sprint, SprintStatus, Step
from coscience.substrate import Substrate
from tests.conftest import write_raw_sprint


def test_load_sprint_parses_plan(substrate):
    write_raw_sprint(
        substrate.repo_root, "sp1", "approved", "cure cancer",
        plan=[{"id": "s1", "run": "echo a"}, {"id": "s2", "run": "echo b"}],
    )
    sprint = substrate.load_sprint("sp1")
    assert sprint.id == "sp1"
    assert sprint.status == SprintStatus.APPROVED
    assert sprint.goals == "cure cancer"
    assert sprint.plan == [Step("s1", "echo a"), Step("s2", "echo b")]


def test_save_then_load_roundtrips(substrate):
    sprint = Sprint(
        id="sp2", status=SprintStatus.EXECUTING, goals="g",
        plan=[Step("s1", "echo a")], program="prog1",
    )
    substrate.save_sprint(sprint)
    loaded = substrate.load_sprint("sp2")
    assert loaded == sprint


def test_iter_sprints_filters_by_status(substrate):
    write_raw_sprint(substrate.repo_root, "sp1", "approved", "g", [{"id": "s", "run": "x"}])
    write_raw_sprint(substrate.repo_root, "sp2", "done", "g", [{"id": "s", "run": "x"}])
    write_raw_sprint(substrate.repo_root, "sp3", "approved", "g", [{"id": "s", "run": "x"}])
    approved = substrate.iter_sprints(status=SprintStatus.APPROVED)
    assert [s.id for s in approved] == ["sp1", "sp3"]


def test_iter_sprints_empty_when_no_dir(substrate):
    assert substrate.iter_sprints() == []
