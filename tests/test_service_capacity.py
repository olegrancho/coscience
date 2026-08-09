import threading

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


def test_concurrent_writes_never_race_on_the_shared_tmp_file(tmp_path):
    # PUT /api/capacity is a sync route, so FastAPI runs concurrent calls in a
    # threadpool for real. A shared ".tmp" name let one thread's os.replace pull
    # the file out from under another thread's write/replace, surfacing as
    # FileNotFoundError. Each thread writes a distinct, individually-valid value
    # so we can also confirm the file that lands is one of the values written,
    # not a torn mix of two writers.
    svc = Service(tmp_path)
    n_threads = 8
    calls_per_thread = 15
    written_values = [{"workers": float(i)}
                       for i in range(n_threads * calls_per_thread)]
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(values):
        for v in values:
            try:
                svc.set_capacity(v)
            except ValueError:
                pass  # a legitimate outcome of validation, not a race symptom
            except Exception as exc:  # noqa: BLE001 - we want to see anything else
                with lock:
                    errors.append(exc)

    threads = [
        threading.Thread(target=worker,
                         args=(written_values[i * calls_per_thread:(i + 1) * calls_per_thread],))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"unexpected non-ValueError errors: {errors!r}"

    path = tmp_path / ".coscience" / "resources.yaml"
    assert path.is_file()
    # No stray per-call tmp file left behind (would get swept into the substrate
    # by the next `git add -A`).
    assert list(path.parent.glob("resources.yaml.*.tmp")) == []
    final = yaml.safe_load(path.read_text())
    assert final in written_values
