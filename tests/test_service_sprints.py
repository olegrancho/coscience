import pytest

from coscience.models import SprintStatus
from coscience.service import NotFoundError, Service


def test_rationale_and_program_surface(tmp_path):
    from coscience.models import Sprint, SprintStatus, Step
    svc = Service(tmp_path)
    svc.substrate.save_sprint(Sprint(id="sp9", status=SprintStatus.PROPOSED, goals="g",
        plan=[Step(id="s1", run="true")], program="prog", rationale="because X"))
    row = svc.list_sprints()[0]
    assert row["rationale"] == "because X"
    assert row["program"] == "prog"
    assert svc.get_sprint("sp9")["rationale"] == "because X"


def test_submit_then_list_and_get(tmp_path):
    svc = Service(tmp_path)
    sid = svc.submit_sprint(id="sp1", goals="cure", plan=[{"id": "s1", "run": "echo hi"}],
                            priority=3, resources_required={"gpu": 1})
    assert sid == "sp1"
    rows = svc.list_sprints()
    assert rows == [{"id": "sp1", "status": "proposed", "goals": "cure",
                     "program": None, "priority": 3, "steps": 1, "results": [],
                     "rationale": "", "resources_required": {"gpu": 1.0}}]
    detail = svc.get_sprint("sp1")
    assert detail["status"] == "proposed"
    assert detail["resources_required"] == {"gpu": 1.0}
    assert detail["plan"] == [{"id": "s1", "run": "echo hi"}]
    assert detail["completed_steps"] == []
    assert detail["lease"] is None


def test_submit_rejects_empty_plan(tmp_path):
    with pytest.raises(ValueError):
        Service(tmp_path).submit_sprint(id="sp1", goals="g", plan=[])


def test_submit_rejects_duplicate_id(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    with pytest.raises(ValueError):
        svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])


def test_approve_changes_status_and_filters(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    svc.approve_sprint("sp1")
    assert svc.get_sprint("sp1")["status"] == "approved"
    assert [r["id"] for r in svc.list_sprints(status="approved")] == ["sp1"]
    assert svc.list_sprints(status="proposed") == []


def test_get_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_sprint("nope")


def test_approve_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).approve_sprint("nope")


def test_reject_moves_proposed_to_canceled(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    svc.reject_sprint("sp1")
    assert svc.get_sprint("sp1")["status"] == "canceled"


def test_reject_non_proposed_raises(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    svc.approve_sprint("sp1")
    with pytest.raises(ValueError):
        svc.reject_sprint("sp1")


def test_reject_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).reject_sprint("nope")


def _executing(svc, sid):
    # Force a sprint into EXECUTING for guard tests (no scheduler needed).
    s = svc.substrate.load_sprint(sid)
    s.status = SprintStatus.EXECUTING
    svc.substrate.save_sprint(s)


def test_edit_proposed_all_fields(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="old", plan=[{"id": "s1", "run": "a"}])
    svc.edit_sprint("sp1", goals="new", plan=[{"id": "s2", "run": "b"}],
                    priority=5, resources_required={"gpu": 2}, preemptible=False)
    d = svc.get_sprint("sp1")
    assert d["goals"] == "new"
    assert d["plan"] == [{"id": "s2", "run": "b"}]
    assert d["priority"] == 5
    assert d["resources_required"] == {"gpu": 2.0}
    assert d["preemptible"] is False


def test_edit_partial_leaves_other_fields(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="keep", plan=[{"id": "s1", "run": "a"}], priority=1)
    svc.edit_sprint("sp1", priority=9)
    d = svc.get_sprint("sp1")
    assert d["goals"] == "keep"
    assert d["priority"] == 9


def test_edit_goals_blocked_when_not_proposed(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    svc.approve_sprint("sp1")
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", goals="nope")


def test_edit_priority_allowed_when_executing(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    _executing(svc, "sp1")
    svc.edit_sprint("sp1", priority=7)
    assert svc.get_sprint("sp1")["priority"] == 7


def test_edit_blocked_when_done(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    s = svc.substrate.load_sprint("sp1")
    s.status = SprintStatus.DONE
    svc.substrate.save_sprint(s)
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", priority=3)


def test_edit_empty_plan_raises(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "a"}])
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", plan=[])


def test_edit_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).edit_sprint("nope", priority=1)
