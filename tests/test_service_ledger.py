import pytest

from coscience.ledger import Ledger
from coscience.models import Result
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service


def test_results_list_and_get(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    assert svc.list_results() == [{"id": "r1", "sprint": "sp1", "summary": "found X"}]
    assert svc.get_result("r1") == {"id": "r1", "sprint": "sp1", "summary": "found X"}


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
