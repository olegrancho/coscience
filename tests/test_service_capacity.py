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
    # This machine's free space is a live measurement, so it cannot be asserted
    # exactly — check it separately and compare the rest (B1).
    assert isinstance(status["hosts"][0].pop("free_gb"), float)
    assert status["hosts"][0].pop("disk") == ""          # a test box is not full
    # A remote host that has never been checked has reported nothing, and a reading
    # the platform does not have must never warn or gate.
    assert status["hosts"][1].pop("free_gb") is None
    assert status["hosts"][1].pop("disk") == ""
    local_health = {"state": "local", "checked_at": 0.0, "last_ok": 0.0, "fail_since": 0.0, "reason": ""}
    unchecked_health = {"state": "unchecked", "checked_at": 0.0, "last_ok": 0.0,
                        "fail_since": 0.0, "reason": ""}
    assert status["hosts"] == [
        {"name": "local", "label": "", "ssh": "", "placeable": True, "programs": None,
         "run_root": "",
         "capacity": {"cpu": 24.0}, "available": {"cpu": 24.0, "workers": 3.0}, "gpus": [],
         "shared": False, "owner": "", "notes": "", "drain": False, "drained_at": 0.0,
         "removing": False, "waiting_on": [], "health": local_health,
         "used": {}, "leases": 0, "leftover": [], "machine": {}, "cards_off": []},
        {"name": "remote1", "label": "", "ssh": "remote1", "placeable": False, "programs": ["p2"],
         "run_root": "~/coscience-runs", "capacity": {"cpu": 28.0}, "available": {}, "gpus": [],
         "shared": False, "owner": "", "notes": "", "drain": False, "drained_at": 0.0,
         "removing": False, "waiting_on": [], "health": unchecked_health,
         "used": {}, "leases": 0, "leftover": [], "machine": {}, "cards_off": []},
    ]


def test_ledger_status_names_each_leases_host(tmp_path):
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    led = Ledger(ResourcePool({"cpu": 4.0}), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"cpu": 1.0}, now=0.0, ttl=60.0)
    assert Service(tmp_path).ledger_status()["leases"][0]["host"] == "local"


def test_ledger_status_names_what_each_lease_is_running(tmp_path):
    """Compute's running-now table and the workers gauge read the title here (P8, P9)."""
    from coscience.ledger import Ledger
    from coscience.models import Sprint, SprintStatus
    from coscience.resources import ResourcePool
    svc = Service(tmp_path)
    svc.substrate.save_sprint(Sprint(id="sp1", status=SprintStatus.APPROVED, goals="g",
                                     plan=["x"], title="Dock the new ligands"))
    led = Ledger(ResourcePool({"cpu": 4.0}), tmp_path / ".coscience" / "leases.json")
    led.load()
    led.acquire("sp1", {"cpu": 1.0}, now=0.0, ttl=60.0)
    led.acquire("gone", {"cpu": 1.0}, now=0.0, ttl=60.0)   # its sprint's files are gone

    titles = {l["sprint_id"]: l["title"] for l in svc.ledger_status()["leases"]}
    assert titles == {"sp1": "Dock the new ligands", "gone": ""}


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
        {"index": 0, "model": "A", "vram_gb": 24.0, "total_vram_gb": None,
         "whole": False, "shared_gb": 8.0}]
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


# --- G1/G2: platform limits and a machine's capacity are edited apart ---------------

def test_platform_limits_set_only_workers_and_housekeepers(tmp_path):
    _write(tmp_path, HOSTS_YAML)
    status = Service(tmp_path).set_platform_limits({"workers": 3, "housekeepers": 1})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert (written["workers"], written["housekeepers"]) == (3.0, 1.0)
    assert written["hosts"]["remote1"]["capacity"] == {"cpu": 28}     # untouched
    assert status["capacity"]["workers"] == 3.0


def test_platform_limits_refuse_anything_else(tmp_path):
    """The old editor was a free-form name/value table; any name could be typed in."""
    for bad in ({"cpu": 8}, {"memory_gb": 4}, {"licence": 1}):
        with pytest.raises(ValueError, match="is not a platform limit"):
            Service(tmp_path).set_platform_limits(bad)
    with pytest.raises(ValueError, match="zero or more"):
        Service(tmp_path).set_platform_limits({"workers": -1})


def test_a_null_limit_removes_the_cap_and_leaves_the_rest(tmp_path):
    _write(tmp_path, "cpu: 24\nworkers: 4\nhousekeepers: 2\n")
    Service(tmp_path).set_platform_limits({"workers": None})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert "workers" not in written
    assert (written["cpu"], written["housekeepers"]) == (24, 2)


def test_saving_this_machine_keeps_the_platform_limits_it_did_not_send(tmp_path):
    """G2: this machine's dialog sends its own amounts only. The caps used to vanish
    unless every save sent them back."""
    _write(tmp_path, "cpu: 24\nworkers: 4\nhousekeepers: 2\n")
    Service(tmp_path).set_capacity({"cpu": 16, "memory_gb": 64})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written == {"cpu": 16.0, "memory_gb": 64.0, "workers": 4, "housekeepers": 2}


def test_platform_limits_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    c = TestClient(build_app(Service(tmp_path)))
    r = c.put("/api/platform-limits", json={"limits": {"workers": 2}})
    assert r.status_code == 200 and r.json()["capacity"]["workers"] == 2.0
    assert c.put("/api/platform-limits", json={"limits": {"cpu": 2}}).status_code == 422


# --- G2 rework: the machine's totals, and what Co-Science may use of them ------------

def test_a_switched_off_card_takes_no_work_and_keeps_every_card_numbered():
    """Card numbers are CUDA device ids: dropping card 0 must not renumber card 1."""
    from coscience.resources import ResourcePool
    pool = ResourcePool.from_dict({"cpu": 8, "gpus": [
        {"model": "A", "vram_gb": 20, "total_vram_gb": 24, "disabled": True},
        {"model": "B", "vram_gb": 10, "total_vram_gb": 24}]})
    local = pool.host("local")
    assert [(g.index, g.model) for g in local.gpus] == [(1, "B")]
    assert [(g.index, g.model) for g in local.cards_off] == [(0, "A")]
    assert local.capacity["gpu"] == 1.0
    assert pool.host_errors == []


def test_a_card_cannot_offer_more_vram_than_it_has():
    from coscience.resources import ResourcePool
    pool = ResourcePool.from_dict({"gpus": [{"model": "A", "vram_gb": 30, "total_vram_gb": 24}]})
    assert "30 GB available is more than the card's 24 GB" in pool.host_errors[0]


def test_this_machines_totals_are_saved_beside_what_is_offered(tmp_path):
    _write(tmp_path, "cpu: 24\nworkers: 4\n")
    status = Service(tmp_path).set_capacity({"cpu": 20, "memory_gb": 40},
                                            machine={"cpu": 32, "memory_gb": 62})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["machine"] == {"cpu": 32.0, "memory_gb": 62.0}
    assert (written["cpu"], written["memory_gb"], written["workers"]) == (20.0, 40.0, 4)
    assert status["hosts"][0]["machine"] == {"cpu": 32.0, "memory_gb": 62.0}


def test_offering_more_than_the_machine_has_is_refused(tmp_path):
    _write(tmp_path, "cpu: 24\n")
    with pytest.raises(ValueError, match="40 available is more than the machine's 32"):
        Service(tmp_path).set_capacity({"cpu": 40}, machine={"cpu": 32})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written == {"cpu": 24}                              # nothing written


def test_a_save_without_totals_keeps_the_ones_on_file(tmp_path):
    _write(tmp_path, "cpu: 24\nmachine: {cpu: 32, memory_gb: 62}\n")
    Service(tmp_path).set_capacity({"cpu": 16})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["machine"] == {"cpu": 32, "memory_gb": 62}


def test_the_ledger_shows_a_servers_totals_and_its_switched_off_cards(tmp_path):
    _write(tmp_path, "cpu: 4\nhosts:\n  g1:\n    ssh: g1\n    capacity: {cpu: 10}\n"
                     "    machine: {cpu: 12, memory_gb: 62}\n"
                     "    gpus:\n      - {model: A, vram_gb: 10, total_vram_gb: 11, disabled: true}\n"
                     "      - {model: B, vram_gb: 11, total_vram_gb: 11}\n")
    g1 = next(h for h in Service(tmp_path).ledger_status()["hosts"] if h["name"] == "g1")
    assert g1["machine"] == {"cpu": 12.0, "memory_gb": 62.0}
    assert [(c["index"], c["model"]) for c in g1["gpus"]] == [(1, "B")]
    assert g1["cards_off"] == [{"index": 0, "model": "A", "vram_gb": 10.0, "total_vram_gb": 11.0}]


def test_a_servers_totals_are_edited_with_it(tmp_path):
    _write(tmp_path, "cpu: 4\nhosts:\n  g1:\n    ssh: g1\n    run_root: ~/r\n    capacity: {cpu: 10}\n")
    svc = Service(tmp_path)
    svc.update_host("g1", capacity={"cpu": 10}, machine={"cpu": 12})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["hosts"]["g1"]["machine"] == {"cpu": 12.0}
    with pytest.raises(ValueError, match="more than the machine's 12"):
        svc.update_host("g1", capacity={"cpu": 14})
