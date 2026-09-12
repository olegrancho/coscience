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


# --- a lease can hand back one key without giving up the rest ------------------
#
# A sprint asleep on a detached job still holds the cpu/gpu that job is using, but
# not the worker slot, which is charged for an agent process that is not running.

def test_releasing_one_key_leaves_the_rest_of_the_lease_standing(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0, "workers": 1.0})
    led.acquire("sp1", {"gpu": 1.0, "workers": 1.0}, now=100.0, ttl=60.0)

    led.release_key("sp1", "workers")
    assert led.available()["workers"] == 1.0      # free for someone else
    assert led.available()["gpu"] == 0.0          # the job still holds it
    assert led.lease_for("sp1") is not None


def test_a_freed_worker_slot_lets_another_sprint_in(tmp_path):
    """The whole point: 15h of GPU training must not block dispatch."""
    led = _ledger(tmp_path, {"cpu": 16.0, "workers": 1.0})
    led.acquire("sleeper", {"cpu": 2.0, "workers": 1.0}, now=100.0, ttl=60.0)
    assert led.acquire("newcomer", {"cpu": 1.0, "workers": 1.0}, now=100.0, ttl=60.0) is None

    led.release_key("sleeper", "workers")
    assert led.acquire("newcomer", {"cpu": 1.0, "workers": 1.0}, now=100.0, ttl=60.0) is not None


def test_taking_a_key_back_respects_capacity(tmp_path):
    led = _ledger(tmp_path, {"workers": 1.0})
    led.acquire("sleeper", {"workers": 1.0}, now=100.0, ttl=60.0)
    led.release_key("sleeper", "workers")
    led.acquire("newcomer", {"workers": 1.0}, now=100.0, ttl=60.0)

    assert led.acquire_key("sleeper", "workers", 1.0) is False   # newcomer has it
    led.release("newcomer")
    assert led.acquire_key("sleeper", "workers", 1.0) is True


def test_taking_back_a_key_already_held_is_a_no_op(tmp_path):
    led = _ledger(tmp_path, {"workers": 2.0})
    led.acquire("sp1", {"workers": 1.0}, now=100.0, ttl=60.0)
    assert led.acquire_key("sp1", "workers", 1.0) is True
    assert led.used()["workers"] == 1.0          # not charged twice


def test_key_moves_survive_a_reload(tmp_path):
    led = _ledger(tmp_path, {"gpu": 1.0, "workers": 1.0})
    led.acquire("sp1", {"gpu": 1.0, "workers": 1.0}, now=100.0, ttl=60.0)
    led.release_key("sp1", "workers")

    again = _ledger(tmp_path, {"gpu": 1.0, "workers": 1.0})
    assert again.available()["workers"] == 1.0
    assert again.available()["gpu"] == 0.0
