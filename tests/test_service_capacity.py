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


HOSTS_YAML = ("cpu: 24\nworkers: 3\n"
              "hosts:\n  remote1:\n    ssh: remote1\n    programs: [p2]\n"
              "    run_root: ~/coscience-runs\n    capacity: {cpu: 28}\n")


def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_ledger_status_lists_every_host(tmp_path):
    _write(tmp_path, HOSTS_YAML)
    status = Service(tmp_path).ledger_status()
    local_health = {"state": "local", "checked_at": 0.0, "last_ok": 0.0, "fail_since": 0.0, "reason": ""}
    unchecked_health = {"state": "unchecked", "checked_at": 0.0, "last_ok": 0.0,
                        "fail_since": 0.0, "reason": ""}
    assert status["hosts"] == [
        {"name": "local", "ssh": "", "placeable": True, "programs": [], "run_root": "",
         "capacity": {"cpu": 24.0}, "available": {"cpu": 24.0, "workers": 3.0}, "gpus": [],
         "shared": False, "owner": "", "notes": "", "drain": False, "drained_at": 0.0, "health": local_health,
         "used": {}, "leases": 0, "leftover": []},
        {"name": "remote1", "ssh": "remote1", "placeable": False, "programs": ["p2"],
         "run_root": "~/coscience-runs", "capacity": {"cpu": 28.0}, "available": {}, "gpus": [],
         "shared": False, "owner": "", "notes": "", "drain": False, "drained_at": 0.0, "health": unchecked_health,
         "used": {}, "leases": 0, "leftover": []},
    ]


def test_ledger_status_names_each_leases_host(tmp_path):
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    led = Ledger(ResourcePool({"cpu": 4.0}), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"cpu": 1.0}, now=0.0, ttl=60.0)
    assert Service(tmp_path).ledger_status()["leases"][0]["host"] == "local"


def test_set_capacity_keeps_the_hosts_section(tmp_path):
    _write(tmp_path, HOSTS_YAML)
    Service(tmp_path).set_capacity({"cpu": 16, "workers": 2})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["cpu"] == 16.0 and written["workers"] == 2.0
    assert written["hosts"]["remote1"]["capacity"] == {"cpu": 28}


def test_set_capacity_refuses_a_resource_named_hosts(tmp_path):
    with pytest.raises(ValueError, match="'hosts' is reserved"):
        Service(tmp_path).set_capacity({"hosts": 1})


def test_ledger_status_reports_a_malformed_host_and_keeps_serving(tmp_path):
    _write(tmp_path, "cpu: 4\nhosts:\n  typo:\n    capacity: {cpu: 8}\n")
    status = Service(tmp_path).ledger_status()
    assert [h["name"] for h in status["hosts"]] == ["local"]
    assert status["capacity"] == {"cpu": 4.0}
    assert status["host_errors"] == ["hosts.typo: needs ssh (an ssh alias or user@host)"]


def test_ledger_status_describes_each_card(tmp_path):
    from coscience.ledger import Ledger
    from coscience.resources import load_pool
    _write(tmp_path, "cpu: 4\ngpus:\n  - {model: A, vram_gb: 24}\n")
    led = Ledger(load_pool(tmp_path), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0)

    status = Service(tmp_path).ledger_status()

    assert status["hosts"][0]["gpus"] == [
        {"index": 0, "model": "A", "vram_gb": 24.0, "whole": False, "shared_gb": 8.0}]
    assert status["leases"][0]["gpu_devices"] == [0]
    assert status["capacity"]["gpu"] == 1.0
    assert status["used"]["gpu"] == 1.0


def test_set_capacity_keeps_the_card_list_and_drops_the_count(tmp_path):
    _write(tmp_path, "cpu: 4\ngpu: 1\ngpus:\n  - {vram_gb: 24}\n")
    Service(tmp_path).set_capacity({"cpu": 8, "gpu": 3})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written == {"cpu": 8.0, "gpus": [{"vram_gb": 24}]}


@pytest.mark.parametrize("name", ["gpus", "gpu_vram_gb"])
def test_set_capacity_refuses_gpu_detail_names(tmp_path, name):
    with pytest.raises(ValueError, match=f"'{name}' is reserved"):
        Service(tmp_path).set_capacity({name: 1})


def test_set_capacity_keeps_the_count_beside_a_malformed_card_list(tmp_path):
    _write(tmp_path, "cpu: 4\ngpu: 1\ngpus: 24GB\n")
    Service(tmp_path).set_capacity({"cpu": 8, "gpu": 1})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written == {"cpu": 8.0, "gpu": 1.0, "gpus": "24GB"}
