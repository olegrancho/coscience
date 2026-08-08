import pytest
import yaml

from coscience.service import Service


def test_set_capacity_writes_the_pool_file(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"cpu": 16, "gpu": 1, "workers": 1})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written == {"cpu": 16.0, "gpu": 1.0, "workers": 1.0}


def test_set_capacity_returns_fresh_ledger_status(tmp_path):
    svc = Service(tmp_path)
    status = svc.set_capacity({"workers": 2})
    assert status["capacity"] == {"workers": 2.0}
    assert status["available"] == {"workers": 2.0}


def test_set_capacity_is_visible_without_restart(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"workers": 3})
    assert svc.pool.capacity == {"workers": 3.0}


def test_set_capacity_accepts_zero(tmp_path):
    svc = Service(tmp_path)
    assert svc.set_capacity({"workers": 0})["capacity"] == {"workers": 0.0}


def test_set_capacity_can_drop_below_current_usage(tmp_path):
    # Draining is legal: the edit is accepted even while more is leased than allowed.
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    led = Ledger(ResourcePool({"workers": 2.0}), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"workers": 1.0}, now=0.0, ttl=60.0)
    led.acquire("sp2", {"workers": 1.0}, now=0.0, ttl=60.0)

    svc = Service(tmp_path)
    status = svc.set_capacity({"workers": 1})
    assert status["capacity"] == {"workers": 1.0}
    assert status["available"] == {"workers": -1.0}     # over-committed, draining
    assert len(status["leases"]) == 2                   # nothing was killed


def test_set_capacity_empties_the_pool(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"cpu": 4})
    assert svc.set_capacity({})["capacity"] == {}


@pytest.mark.parametrize("bad", [
    {"cpu": -1},
    {"cpu": float("nan")},
    {"cpu": float("inf")},
    {"cpu": "eight"},
    {"cpu": None},
    {"cpu": True},
    {"": 1},
    {"  ": 1},
    {"resources": 1},
])
def test_set_capacity_rejects_bad_input(tmp_path, bad):
    with pytest.raises(ValueError):
        Service(tmp_path).set_capacity(bad)


def test_set_capacity_rejects_duplicate_names_after_trimming(tmp_path):
    with pytest.raises(ValueError):
        Service(tmp_path).set_capacity({"cpu": 1, " cpu ": 2})


def test_rejected_input_leaves_the_file_untouched(tmp_path):
    svc = Service(tmp_path)
    svc.set_capacity({"cpu": 4})
    with pytest.raises(ValueError):
        svc.set_capacity({"cpu": -1})
    assert svc.pool.capacity == {"cpu": 4.0}
