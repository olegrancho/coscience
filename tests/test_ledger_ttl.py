from coscience.ledger import Ledger
from coscience.resources import ResourcePool


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_expire_removes_only_stale(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    led.acquire("old", {"gpu": 1.0}, now=0.0, ttl=10.0)     # expires at 10
    led.acquire("fresh", {"gpu": 1.0}, now=0.0, ttl=100.0)  # expires at 100
    removed = led.expire(now=50.0)
    assert [r.sprint_id for r in removed] == ["old"]
    assert led.lease_for("old") is None
    assert led.lease_for("fresh") is not None


def test_renew_extends_expiry(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("sp1", {"gpu": 1.0}, now=0.0, ttl=10.0)
    led.renew("sp1", now=8.0, ttl=10.0)  # now expires at 18
    assert led.expire(now=12.0) == []
    assert led.lease_for("sp1") is not None


def test_expire_frees_capacity_for_reacquire(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("stuck", {"gpu": 1.0}, now=0.0, ttl=10.0)
    assert led.acquire("next", {"gpu": 1.0}, now=20.0, ttl=10.0) is None  # still held in-memory
    led.expire(now=20.0)
    assert led.acquire("next", {"gpu": 1.0}, now=20.0, ttl=10.0) is not None
