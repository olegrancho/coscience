"""O2: the pool is a list of hosts; top-level amounts are the local host."""
import pytest

from coscience.resources import LOCAL, ResourcePool, over_capacity

FILE = {
    "cpu": 24, "gpu": 1, "workers": 3, "housekeepers": 2,
    "hosts": {
        "remote1": {"ssh": "remote1", "run_root": "~/coscience-runs", "programs": ["p2"],
                 "capacity": {"cpu": 28, "memory_gb": 28}},
    },
}


def test_top_level_amounts_are_the_local_host_and_platform_keys_stay_pool_wide():
    pool = ResourcePool.from_dict(FILE)
    local = pool.host(LOCAL)
    assert local.capacity == {"cpu": 24.0, "gpu": 1.0}
    assert local.is_local and local.placeable and local.allows("p5")
    assert pool.capacity == {"cpu": 24.0, "gpu": 1.0, "workers": 3.0, "housekeepers": 2.0}


def test_a_remote_host_is_parsed_but_not_placeable_until_remote_launch_exists():
    pool = ResourcePool.from_dict(FILE)
    remote1 = pool.host("remote1")
    assert remote1.ssh == "remote1" and remote1.run_root == "~/coscience-runs"
    assert remote1.capacity == {"cpu": 28.0, "memory_gb": 28.0}
    assert not remote1.is_local and not remote1.placeable
    assert [h.name for h in pool.placeable_hosts("p2")] == [LOCAL]


def test_a_reserved_host_admits_only_its_programs():
    remote1 = ResourcePool.from_dict(FILE).host("remote1")
    assert remote1.allows("p2")
    assert not remote1.allows("p5")
    assert not remote1.allows(None)


def test_placeable_totals_include_a_live_remote_host(every_host_placeable):
    pool = ResourcePool.from_dict(FILE)
    assert pool.capacity["cpu"] == 52.0
    assert [h.name for h in pool.placeable_hosts("p5")] == [LOCAL]
    assert [h.name for h in pool.placeable_hosts("p2")] == [LOCAL, "remote1"]


def test_a_flat_pool_is_one_local_host():
    pool = ResourcePool({"gpu": 1.0, "workers": 2.0})
    assert [h.name for h in pool.hosts] == [LOCAL]
    assert pool.host(LOCAL).capacity == {"gpu": 1.0}
    assert pool.capacity == {"gpu": 1.0, "workers": 2.0}


def test_the_legacy_resources_wrapper_still_reads():
    pool = ResourcePool.from_dict({"resources": {"gpu": 1}})
    assert pool.capacity == {"gpu": 1.0}
    assert pool.host(LOCAL).capacity == {"gpu": 1.0}


@pytest.mark.parametrize("hosts, message", [
    ({"local": {"ssh": "x"}}, "'local' is this machine"),
    ({"b": {"capacity": {"cpu": 1}}}, "needs ssh"),
    ({"b": {"ssh": "b", "capacity": {"workers": 1}}}, "platform-wide"),
    ({"b": {"ssh": "b", "capacity": {"cpu": -1}}}, "non-negative number"),
    ({"b": {"ssh": "b", "capacity": {"cpu": True}}}, "non-negative number"),
    ({"b": {"ssh": "b", "capacity": {"cpu": float("inf")}}}, "non-negative number"),
    ({"b": {"ssh": "b", "capacity": {"cpu": float("nan")}}}, "non-negative number"),
    ({"b": {"ssh": "b", "programs": "p2"}}, "programs: must be a list"),
    ({"b": "remote1"}, "must be a mapping"),
    ({"b": {"ssh": "-oProxyCommand=x"}}, "hosts.b.ssh"),
    ({"b": {"ssh": "b", "drain": "yes"}}, "hosts.b.drain: must be true or false"),
])
def test_a_malformed_host_is_skipped_and_named(hosts, message):
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": hosts})
    assert [h.name for h in pool.hosts] == [LOCAL]          # local work is unaffected
    assert pool.capacity == {"cpu": 1.0}
    assert len(pool.host_errors) == 1 and message in pool.host_errors[0]


def test_a_hosts_drained_at_is_parsed_and_defaults_to_zero():
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {
        "b": {"ssh": "b", "drain": True, "drained_at": 1234.5},
        "c": {"ssh": "c"}}})
    assert pool.host("b").drained_at == 1234.5
    assert pool.host("c").drained_at == 0.0


def test_a_non_numeric_drained_at_is_ignored():
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"b": {"ssh": "b", "drained_at": "soon"}}})
    assert pool.host("b").drained_at == 0.0


def test_a_malformed_host_does_not_take_its_neighbours_down():
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {
        "typo": {"capacity": {"cpu": 1}},
        "remote1": {"ssh": "remote1", "capacity": {"cpu": 28}}}})
    assert [h.name for h in pool.hosts] == [LOCAL, "remote1"]
    assert pool.host_errors == ["hosts.typo: needs ssh (an ssh alias or user@host)"]


def test_hosts_that_are_not_a_mapping_are_reported():
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": ["remote1"]})
    assert [h.name for h in pool.hosts] == [LOCAL]
    assert pool.host_errors == ["hosts: must be a mapping of host name to host"]


def test_over_capacity_asks_whether_any_allowed_host_holds_the_whole_request():
    pool = ResourcePool.from_dict(FILE)
    assert over_capacity({"cpu": 24.0, "workers": 1.0}, pool, "p2") == {}
    assert over_capacity({"cpu": 28.0}, pool, "p2") == {"cpu": (28.0, 24.0)}   # remote1 not placeable yet
    assert over_capacity({"workers": 4.0}, pool, "p2") == {"workers": (4.0, 3.0)}


def test_over_capacity_respects_reservation_when_remote_hosts_are_live(every_host_placeable):
    pool = ResourcePool.from_dict(FILE)
    assert over_capacity({"cpu": 28.0}, pool, "p2") == {}
    assert over_capacity({"cpu": 28.0}, pool, "p5") == {"cpu": (28.0, 24.0)}


def test_over_capacity_does_not_add_cpu_across_hosts(every_host_placeable):
    pool = ResourcePool.from_dict(FILE)
    # 52 cpu in total, but no single host has 30
    assert over_capacity({"cpu": 30.0}, pool, "p2") == {"cpu": (30.0, 28.0)}
