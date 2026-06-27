import pytest

from coscience.ledger import Ledger
from coscience.models import Result, Sprint
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service


def test_results_list_and_get(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    # completed_at falls back to the file mtime, so just check it's a timestamp
    row = svc.list_results()[0]
    assert isinstance(row.pop("completed_at"), float)
    assert row == {"id": "r1", "sprint": "sp1", "summary": "found X"}
    # sprint sp1 doesn't exist yet → program resolves to None, not an error
    detail = svc.get_result("r1")
    assert isinstance(detail.pop("completed_at"), float)
    assert detail == {"id": "r1", "sprint": "sp1", "summary": "found X", "program": None}


def test_result_completed_at_explicit_wins(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="x", completed_at=1700000000.0))
    assert svc.get_result("r1")["completed_at"] == 1700000000.0


def test_get_result_links_to_program(tmp_path):
    from coscience.models import SprintStatus, Step
    svc = Service(tmp_path)
    svc.substrate.save_sprint(Sprint(id="sp1", status=SprintStatus.DONE, goals="g",
        plan=[Step(id="s1", run="true")], program="prog1"))
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    assert svc.get_result("r1")["program"] == "prog1"


def test_get_missing_result_raises(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_result("nope")


def test_ledger_status_reflects_leases(tmp_path):
    pool = ResourcePool({"gpu": 2.0})
    # seed a lease on disk
    led = Ledger(pool, tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)

    svc = Service(tmp_path, pool=pool)
    status = svc.ledger_status()
    assert status["capacity"] == {"gpu": 2.0}
    assert status["available"] == {"gpu": 1.0}
    assert [lease["sprint_id"] for lease in status["leases"]] == ["sp1"]


def test_get_sprint_lease_includes_sprint_id(tmp_path):
    pool = ResourcePool({"gpu": 2.0})
    led = Ledger(pool, tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)

    svc = Service(tmp_path, pool=pool)
    svc.submit_sprint(id="sp1", goals="g", plan=[{"id": "s1", "run": "true"}])
    lease = svc.get_sprint("sp1")["lease"]
    assert lease is not None
    assert lease["sprint_id"] == "sp1"
