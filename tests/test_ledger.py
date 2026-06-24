from coscience.ledger import Ledger
from coscience.resources import ResourcePool


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_acquire_within_capacity(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    lease = led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    assert lease is not None
    assert led.used() == {"gpu": 1.0}
    assert led.available() == {"gpu": 1.0}


def test_all_or_nothing_when_overcommitted(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    assert led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0) is not None
    assert led.acquire("sp2", {"gpu": 1.0}, now=100.0, ttl=60.0) is None
    assert led.used() == {"gpu": 1.0}


def test_multi_resource_all_or_nothing(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0, "cpu": 4.0})
    # cpu fits but gpu does not -> whole request denied
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    assert led.acquire("sp2", {"gpu": 1.0, "cpu": 2.0}, now=100.0, ttl=60.0) is None
    assert led.used() == {"gpu": 1.0}


def test_acquire_is_idempotent_per_sprint(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    a = led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    b = led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    assert a.id == b.id
    assert led.used() == {"gpu": 1.0}


def test_release_frees_capacity(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0)
    led.release("sp1")
    assert led.used() == {"gpu": 0.0}
    assert led.acquire("sp2", {"gpu": 1.0}, now=100.0, ttl=60.0) is not None


def test_persistence_roundtrip(tmp_path):
    led = _ledger(tmp_path, {"gpu": 2.0})
    led.acquire("sp1", {"gpu": 1.0}, now=100.0, ttl=60.0, priority=3)
    led2 = Ledger(ResourcePool({"gpu": 2.0}), tmp_path / "leases.json")
    led2.load()
    lease = led2.lease_for("sp1")
    assert lease is not None and lease.priority == 3
    assert led2.used() == {"gpu": 1.0}


def test_can_fit_unknown_key_is_false(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0})
    assert led.can_fit({"tpu": 1.0}) is False
