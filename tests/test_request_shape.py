"""O4: a sprint's request records whether its work may span hosts."""
from coscience.models import Sprint, SprintStatus
from coscience.service import Service


def test_distributed_defaults_off_and_is_not_written(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"]))
    assert substrate.load_sprint("s1").distributed is False
    assert "distributed" not in (substrate.sprint_dir("s1") / "sprint.md").read_text()


def test_distributed_round_trips(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"],
                                 distributed=True))
    assert substrate.load_sprint("s1").distributed is True


def test_submit_edit_and_read_carry_distributed(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="s1", goals="g", plan=["a"], resources_required={"cpu": 64},
                      distributed=True)
    assert svc.get_sprint("s1")["distributed"] is True
    assert [row["distributed"] for row in svc.list_sprints()] == [True]
    svc.edit_sprint("s1", distributed=False)
    assert svc.get_sprint("s1")["distributed"] is False
