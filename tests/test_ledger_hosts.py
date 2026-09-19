"""O2: a lease names its host, and a grant never spans two machines."""
from coscience.ledger import Ledger
from coscience.resources import LOCAL, ResourcePool


def _ledger(tmp_path, pool):
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    return led


def _two_hosts(programs=None):
    big = {"ssh": "big", "capacity": {"cpu": 16}}
    if programs is not None:
        big["programs"] = programs
    return ResourcePool.from_dict({"cpu": 4, "workers": 2, "hosts": {"big": big}})


def test_a_lease_names_the_local_host_by_default(tmp_path):
    led = _ledger(tmp_path, ResourcePool({"gpu": 1.0}))
    assert led.acquire("sp1", {"gpu": 1.0}, now=0.0, ttl=60.0).host == LOCAL


def test_a_lease_file_written_before_hosts_loads_as_local(tmp_path):
    (tmp_path / "leases.json").write_text(
        '[{"id": "l1", "sprint_id": "sp1", "amounts": {"gpu": 1.0}, "granted_at": 0.0,'
        ' "expires_at": 1e12, "priority": 0, "preemptible": true}]')
    led = _ledger(tmp_path, ResourcePool({"gpu": 1.0}))
    assert led.lease_for("sp1").host == LOCAL
    assert led.available() == {"gpu": 0.0}


def test_a_remote_host_takes_no_lease_before_remote_launch_exists(tmp_path):
    led = _ledger(tmp_path, _two_hosts())
    assert led.acquire("large", {"cpu": 8.0}, now=0.0, ttl=60.0) is None


def test_the_first_host_that_holds_the_whole_request_is_chosen(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    assert led.acquire("small", {"cpu": 4.0}, now=0.0, ttl=60.0).host == LOCAL
    assert led.acquire("large", {"cpu": 8.0}, now=0.0, ttl=60.0).host == "big"


def test_cpu_on_two_hosts_is_not_one_pool(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    led.acquire("a", {"cpu": 12.0}, now=0.0, ttl=60.0)             # big; 4 left there
    assert led.available()["cpu"] == 8.0                             # 4 local + 4 big
    assert led.acquire("b", {"cpu": 8.0}, now=0.0, ttl=60.0) is None
    assert led.used("big") == {"cpu": 12.0}
    assert led.available(LOCAL) == {"cpu": 4.0, "workers": 2.0}


def test_a_reserved_host_is_never_granted_to_another_program(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts(programs=["p2"]))
    assert led.acquire("p5-c1", {"cpu": 8.0}, now=0.0, ttl=60.0, program="p5") is None
    assert led.acquire("p5-c2", {"cpu": 4.0}, now=0.0, ttl=60.0, program="p5").host == LOCAL
    assert led.acquire("p2-c1", {"cpu": 8.0}, now=0.0, ttl=60.0, program="p2").host == "big"


def test_platform_keys_are_counted_across_hosts(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    led.acquire("a", {"cpu": 1.0, "workers": 1.0}, now=0.0, ttl=60.0)
    led.acquire("b", {"cpu": 16.0, "workers": 1.0}, now=0.0, ttl=60.0)
    assert led.available(LOCAL)["workers"] == 0.0
    assert led.available("big")["workers"] == 0.0
    assert led.acquire("c", {"cpu": 1.0, "workers": 1.0}, now=0.0, ttl=60.0) is None


def test_fit_host_counts_grants_not_yet_written(tmp_path, every_host_placeable):
    led = _ledger(tmp_path, _two_hosts())
    assert led.fit_host({"cpu": 16.0}) == "big"
    assert led.fit_host({"cpu": 16.0}, pending=[("big", {"cpu": 16.0})]) is None
    assert led.fit_host({"workers": 1.0},
                        pending=[("big", {"workers": 1.0}), (LOCAL, {"workers": 1.0})]) is None


def test_a_reacquired_key_must_fit_on_the_leases_own_host(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 16, "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}}}})
    led = _ledger(tmp_path, pool)
    led.acquire("x", {"cpu": 16.0}, now=0.0, ttl=60.0)              # fills local
    assert led.acquire("a", {"cpu": 16.0}, now=0.0, ttl=60.0).host == "big"
    led.release_key("a", "cpu")
    assert led.acquire("b", {"cpu": 16.0}, now=0.0, ttl=60.0).host == "big"
    led.release("x")                                                 # local is free again
    assert led.acquire_key("a", "cpu", 16.0) is False                # but a's lease is on big


def test_a_local_lease_is_saved_in_the_format_older_code_reads(tmp_path):
    import json
    led = _ledger(tmp_path, ResourcePool({"cpu": 1.0}))
    led.acquire("sp1", {"cpu": 1.0}, now=0.0, ttl=60.0)
    [entry] = json.loads((tmp_path / "leases.json").read_text())
    assert set(entry) == {"id", "sprint_id", "amounts", "granted_at", "expires_at",
                          "priority", "preemptible"}


def test_a_remote_lease_keeps_its_host_on_disk(tmp_path, every_host_placeable):
    import json
    led = _ledger(tmp_path, _two_hosts())
    led.acquire("large", {"cpu": 8.0}, now=0.0, ttl=60.0)
    [entry] = json.loads((tmp_path / "leases.json").read_text())
    assert entry["host"] == "big"
    assert _ledger(tmp_path, _two_hosts()).lease_for("large").host == "big"


def test_a_lease_file_with_fields_this_code_does_not_know_still_loads(tmp_path):
    (tmp_path / "leases.json").write_text(
        '[{"id": "l1", "sprint_id": "sp1", "amounts": {"gpu": 1.0}, "granted_at": 0.0,'
        ' "expires_at": 1e12, "priority": 0, "preemptible": true, "gpu_devices": [0]}]')
    led = _ledger(tmp_path, ResourcePool({"gpu": 1.0}))
    assert led.lease_for("sp1").host == LOCAL


# --- platform-wide slots are not a program's work (2026-09-18 regression) ------

def _all_hosts_restricted():
    """Every server carries an explicit `programs:` list, so no server admits a
    request that names no program — the state the dashboard produces once someone
    keeps one program off this machine."""
    return ResourcePool.from_dict(
        {"cpu": 4, "workers": 2, "housekeepers": 2, "programs": ["p1"],
         "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}, "programs": ["p2"]}}})


def test_a_housekeeper_slot_is_granted_when_no_server_admits_a_program_less_request(tmp_path):
    # The PM and wiki loops take this slot before reasoning. It is pool-wide
    # bookkeeping, so per-program server access must not gate it — when it did,
    # both loops idled silently instead of running.
    led = _ledger(tmp_path, _all_hosts_restricted())
    assert led.pool.grantable_hosts(None) == []
    lease = led.acquire("pm:p1", {"housekeepers": 1.0}, now=0.0, ttl=60.0)
    assert lease is not None and lease.host == LOCAL


def test_a_worker_slot_alone_is_granted_the_same_way(tmp_path):
    led = _ledger(tmp_path, _all_hosts_restricted())
    assert led.acquire("slot", {"workers": 1.0}, now=0.0, ttl=60.0) is not None


def test_real_work_without_a_program_still_respects_server_access(tmp_path):
    # Only platform keys bypass access: a request for actual resources does not.
    led = _ledger(tmp_path, _all_hosts_restricted())
    assert led.acquire("sp1", {"cpu": 1.0}, now=0.0, ttl=60.0) is None
    assert led.acquire("sp2", {"cpu": 1.0, "workers": 1.0}, now=0.0, ttl=60.0) is None


def test_a_housekeeper_slot_still_runs_out(tmp_path):
    led = _ledger(tmp_path, _all_hosts_restricted())
    assert led.acquire("h1", {"housekeepers": 1.0}, now=0.0, ttl=60.0) is not None
    assert led.acquire("h2", {"housekeepers": 1.0}, now=0.0, ttl=60.0) is not None
    assert led.acquire("h3", {"housekeepers": 1.0}, now=0.0, ttl=60.0) is None
