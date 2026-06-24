import pytest

from coscience.service import NotFoundError, Service


def test_submit_then_list_and_get(tmp_path):
    svc = Service(tmp_path)
    sid = svc.submit_sprint(id="sp1", goals="cure", plan=[{"id": "s1", "run": "echo hi"}],
                            priority=3, resources_required={"gpu": 1})
    assert sid == "sp1"
    rows = svc.list_sprints()
    assert rows == [{"id": "sp1", "status": "proposed", "goals": "cure",
                     "priority": 3, "steps": 1, "results": []}]
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
