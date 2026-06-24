import json

from coscience.models import Result
from coscience.service import Service


def test_submit_approve_flow_and_results(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="cure cancer",
                      plan=[{"id": "s1", "run": "echo a"}, {"id": "s2", "run": "echo b"}],
                      priority=5, resources_required={"gpu": 1})
    assert [r["id"] for r in svc.list_sprints(status="proposed")] == ["sp1"]

    svc.approve_sprint("sp1")
    assert [r["id"] for r in svc.list_sprints(status="approved")] == ["sp1"]

    detail = svc.get_sprint("sp1")
    assert detail["priority"] == 5
    assert len(detail["plan"]) == 2

    svc.substrate.save_result(Result(id="sp1-result", sprint="sp1", summary="done"))
    assert svc.get_result("sp1-result")["summary"] == "done"


def test_every_return_value_is_json_serialisable(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}],
                      resources_required={"gpu": 1})
    svc.approve_sprint("sp1")
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="x"))
    # None of these should raise TypeError on json.dumps.
    json.dumps(svc.list_sprints())
    json.dumps(svc.get_sprint("sp1"))
    json.dumps(svc.list_results())
    json.dumps(svc.get_result("r1"))
    json.dumps(svc.ledger_status())
