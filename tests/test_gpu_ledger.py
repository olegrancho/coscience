"""O3: GPU grants take specific cards, whole or as VRAM shares."""
import json

from coscience.ledger import Ledger
from coscience.resources import LOCAL, ResourcePool


def _ledger(tmp_path, pool):
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    return led


def _cards(*vram):
    return ResourcePool.from_dict({"cpu": 16, "gpus": [{"vram_gb": v} for v in vram]})


def test_a_whole_card_request_takes_the_lowest_free_card(tmp_path):
    led = _ledger(tmp_path, _cards(24, 24))
    assert led.acquire("a", {"gpu": 1}, now=0.0, ttl=60.0).gpu_devices == [0]
    assert led.acquire("b", {"gpu": 1}, now=0.0, ttl=60.0).gpu_devices == [1]
    assert led.acquire("c", {"gpu": 1}, now=0.0, ttl=60.0) is None


def test_shares_fit_together_on_one_card(tmp_path):
    led = _ledger(tmp_path, _cards(24))
    assert led.acquire("a", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0).gpu_devices == [0]
    assert led.acquire("b", {"gpu_vram_gb": 16}, now=0.0, ttl=60.0).gpu_devices == [0]
    assert led.acquire("c", {"gpu_vram_gb": 1}, now=0.0, ttl=60.0) is None
    assert led.device_use(LOCAL) == {0: (False, 24.0)}


def test_a_card_lent_whole_takes_no_share_and_a_shared_card_is_not_lent_whole(tmp_path):
    led = _ledger(tmp_path, _cards(24))
    led.acquire("whole", {"gpu": 1}, now=0.0, ttl=60.0)
    assert led.acquire("share", {"gpu_vram_gb": 1}, now=0.0, ttl=60.0) is None
    led.release("whole")
    assert led.acquire("share", {"gpu_vram_gb": 1}, now=0.0, ttl=60.0) is not None
    assert led.acquire("whole", {"gpu": 1}, now=0.0, ttl=60.0) is None


def test_a_share_goes_to_the_tightest_card_that_fits(tmp_path):
    led = _ledger(tmp_path, _cards(48, 24))
    assert led.acquire("small", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0).gpu_devices == [1]
    assert led.acquire("large", {"gpu_vram_gb": 40}, now=0.0, ttl=60.0).gpu_devices == [0]


def test_two_shared_cards_are_two_different_cards(tmp_path):
    led = _ledger(tmp_path, _cards(24, 24))
    pair = led.acquire("pair", {"gpu": 2, "gpu_vram_gb": 20}, now=0.0, ttl=60.0)
    assert pair.gpu_devices == [0, 1]
    assert led.acquire("more", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0) is None


def test_a_card_of_undeclared_vram_is_only_lent_whole(tmp_path):
    led = _ledger(tmp_path, ResourcePool({"gpu": 1.0}))
    assert led.acquire("share", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0) is None
    assert led.acquire("whole", {"gpu": 1.0}, now=0.0, ttl=60.0).gpu_devices == [0]


def test_gpu_usage_counts_cards_held(tmp_path):
    led = _ledger(tmp_path, _cards(24, 24))
    led.acquire("a", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0)
    led.acquire("b", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0)
    assert led.used()["gpu"] == 1.0
    assert led.available()["gpu"] == 1.0
    assert led.available(LOCAL)["gpu"] == 1.0


def test_card_lists_are_saved_and_reloaded(tmp_path):
    led = _ledger(tmp_path, _cards(24))
    led.acquire("a", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0)
    [entry] = json.loads((tmp_path / "leases.json").read_text())
    assert entry["gpu_devices"] == [0]
    assert _ledger(tmp_path, _cards(24)).lease_for("a").gpu_devices == [0]


def test_a_lease_without_cards_is_saved_without_the_field(tmp_path):
    led = _ledger(tmp_path, _cards(24))
    led.acquire("cpu-only", {"cpu": 1}, now=0.0, ttl=60.0)
    [entry] = json.loads((tmp_path / "leases.json").read_text())
    assert "gpu_devices" not in entry


def test_an_old_gpu_lease_is_given_its_card_on_load(tmp_path):
    (tmp_path / "leases.json").write_text(json.dumps([{
        "id": "l1", "sprint_id": "old", "amounts": {"gpu": 1.0}, "granted_at": 0.0,
        "expires_at": 1e12, "priority": 0, "preemptible": True}]))
    led = _ledger(tmp_path, _cards(24, 24))
    assert led.lease_for("old").gpu_devices == [0]
    assert led.acquire("new", {"gpu": 1}, now=0.0, ttl=60.0).gpu_devices == [1]


def test_can_fit_asks_for_free_cards(tmp_path):
    led = _ledger(tmp_path, _cards(24))
    assert led.can_fit({"gpu_vram_gb": 24})
    led.acquire("a", {"gpu": 1}, now=0.0, ttl=60.0)
    assert not led.can_fit({"gpu_vram_gb": 1})
    assert not led.can_fit({"gpu": 1}, LOCAL)


def test_a_preferred_free_card_is_taken_over_the_lowest(tmp_path):
    led = _ledger(tmp_path, _cards(24, 24))
    assert led.acquire("a", {"gpu": 1}, now=0.0, ttl=60.0, prefer_cards=[1]).gpu_devices == [1]


def test_a_preferred_card_that_is_taken_falls_back_to_normal_placement(tmp_path):
    led = _ledger(tmp_path, _cards(24, 24))
    led.acquire("b", {"gpu": 1}, now=0.0, ttl=60.0, prefer_cards=[1])
    assert led.acquire("a", {"gpu": 1}, now=0.0, ttl=60.0, prefer_cards=[1]).gpu_devices == [0]


def test_a_preferred_card_is_kept_for_a_share(tmp_path):
    led = _ledger(tmp_path, _cards(48, 24))
    assert led.acquire("a", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0, prefer_cards=[0]).gpu_devices == [0]
