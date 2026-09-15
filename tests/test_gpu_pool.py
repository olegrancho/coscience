"""O3: GPUs are cards with VRAM, declared per host."""
import pytest

from coscience.resources import LOCAL, Gpu, ResourcePool, gpu_request, over_capacity


def test_a_card_list_describes_the_local_host():
    pool = ResourcePool.from_dict({"cpu": 8, "gpus": [{"model": "A", "vram_gb": 24},
                                                      {"vram_gb": 48}]})
    local = pool.host(LOCAL)
    assert local.gpus == [Gpu(0, 24.0, "A"), Gpu(1, 48.0, "")]
    assert local.capacity == {"cpu": 8.0, "gpu": 2.0}
    assert pool.capacity == {"cpu": 8.0, "gpu": 2.0}
    assert pool.host_errors == []


def test_a_bare_gpu_count_is_cards_of_undeclared_vram():
    assert ResourcePool.from_dict({"gpu": 2}).host(LOCAL).gpus == [Gpu(0), Gpu(1)]
    assert ResourcePool({"gpu": 1.0}).host(LOCAL).gpus == [Gpu(0)]


def test_a_card_list_that_contradicts_the_count_wins_and_is_reported():
    pool = ResourcePool.from_dict({"gpu": 1, "gpus": [{"vram_gb": 24}, {"vram_gb": 24}]})
    assert pool.host(LOCAL).capacity["gpu"] == 2.0
    assert pool.host_errors == ["gpus: 2 card(s) listed but gpu is 1; using the list"]


@pytest.mark.parametrize("gpus, message", [
    ("24GB", "gpus: must be a list of cards"),
    ([24], "gpus[0]: must be a mapping with vram_gb"),
    ([{"model": "A"}], "gpus[0].vram_gb: must be a positive number"),
    ([{"vram_gb": 0}], "gpus[0].vram_gb: must be a positive number"),
    ([{"vram_gb": True}], "gpus[0].vram_gb: must be a positive number"),
    ([{"vram_gb": float("inf")}], "gpus[0].vram_gb: must be a positive number"),
])
def test_a_malformed_local_card_list_falls_back_to_the_count(gpus, message):
    pool = ResourcePool.from_dict({"gpu": 1, "gpus": gpus})
    assert pool.host(LOCAL).gpus == [Gpu(0)]
    assert pool.host_errors == [message]


def test_a_remote_host_lists_its_cards():
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"remote1": {
        "ssh": "remote1", "capacity": {"cpu": 12}, "gpus": [{"vram_gb": 11}]}}})
    host = pool.host("remote1")
    assert host.gpus == [Gpu(0, 11.0)]
    assert host.capacity == {"cpu": 12.0, "gpu": 1.0}


def test_a_remote_host_with_contradicting_cards_is_skipped():
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"remote1": {
        "ssh": "remote1", "capacity": {"gpu": 2}, "gpus": [{"vram_gb": 11}]}}})
    assert pool.host("remote1") is None
    assert pool.host_errors == ["hosts.remote1: gpus lists 1 card(s) but capacity.gpu is 2"]


def test_gpu_request_reads_whole_cards_and_shares():
    assert gpu_request({}) == (0, None)
    assert gpu_request({"cpu": 4}) == (0, None)
    assert gpu_request({"gpu": 2}) == (2, None)
    assert gpu_request({"gpu_vram_gb": 8}) == (1, 8.0)
    assert gpu_request({"gpu": 2, "gpu_vram_gb": 8}) == (2, 8.0)
    assert gpu_request({"gpu": 0.5}) == (1, None)


def test_over_capacity_judges_cards_not_a_summed_count():
    pool = ResourcePool.from_dict({"gpus": [{"vram_gb": 24}]})
    assert over_capacity({"gpu": 1}, pool) == {}
    assert over_capacity({"gpu": 2}, pool) == {"gpu": (2.0, 1.0)}
    assert over_capacity({"gpu_vram_gb": 16}, pool) == {}
    assert over_capacity({"gpu_vram_gb": 32}, pool) == {"gpu_vram_gb": (32.0, 24.0)}


def test_a_share_needs_declared_vram():
    pool = ResourcePool.from_dict({"gpu": 1})
    assert over_capacity({"gpu": 1}, pool) == {}
    assert over_capacity({"gpu_vram_gb": 8}, pool) == {"gpu_vram_gb": (8.0, 0.0)}


def test_a_vram_amount_given_as_capacity_is_reported_and_ignored():
    pool = ResourcePool.from_dict({"cpu": 1, "gpu_vram_gb": 24})
    assert pool.capacity == {"cpu": 1.0}
    assert pool.host_errors == ["gpu_vram_gb: is a request key, not capacity; declare cards under gpus:"]


@pytest.mark.parametrize("key", ["gpu_vram_gb", "gpus"])
def test_gpu_detail_under_a_remote_hosts_capacity_is_refused(key):
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"remote1": {
        "ssh": "remote1", "capacity": {key: 24}}}})
    assert pool.host("remote1") is None
    assert pool.host_errors == [
        f"hosts.remote1.capacity.{key}: GPU detail belongs under gpus:, not capacity"]


def test_a_fractional_gpu_count_is_reported_and_rounded_down():
    pool = ResourcePool.from_dict({"gpu": 1.5})
    assert pool.capacity == {"gpu": 1.0}
    assert pool.host(LOCAL).gpus == [Gpu(0)]
    assert pool.host_errors == ["gpu: 1.5 is not a whole number of cards; using 1"]
