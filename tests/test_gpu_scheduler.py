"""O3: one cycle's grants and preemption respect individual cards."""
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio)


def _ledger(tmp_path, *vram):
    led = Ledger(ResourcePool.from_dict({"cpu": 16, "gpus": [{"vram_gb": v} for v in vram]}),
                 tmp_path / "leases.json")
    led.load()
    return led


def test_one_cycle_does_not_lend_the_same_card_whole_twice(tmp_path):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path, 24)
    granted = pol.select_grants([_sprint("a", req={"gpu": 1}), _sprint("b", req={"gpu": 1})],
                                {"a": 0.0, "b": 1.0}, led, now=0.0)
    assert [s.id for s in granted] == ["a"]


def test_one_cycle_packs_shares_onto_a_card_until_it_is_full(tmp_path):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path, 24)
    sprints = [_sprint(s, req={"gpu_vram_gb": 10}) for s in ("a", "b", "c")]
    granted = pol.select_grants(sprints, {"a": 0.0, "b": 1.0, "c": 2.0}, led, now=0.0)
    assert [s.id for s in granted] == ["a", "b"]


def test_a_whole_card_candidate_preempts_every_share_on_that_card(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, 24)
    led.acquire("s1", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0, priority=0)
    led.acquire("s2", {"gpu_vram_gb": 8}, now=1.0, ttl=60.0, priority=0)
    led.acquire("cpu", {"cpu": 4}, now=2.0, ttl=60.0, priority=0)
    cand = _sprint("whole", prio=5, req={"gpu": 1})
    victims = pol.select_yield_victims(cand, 5, led, {"s1", "s2", "cpu"})
    assert sorted(v.sprint_id for v in victims) == ["s1", "s2"]


def test_a_share_candidate_frees_only_enough_vram(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, 24)
    led.acquire("big", {"gpu_vram_gb": 16}, now=0.0, ttl=60.0, priority=0)
    led.acquire("small", {"gpu_vram_gb": 8}, now=1.0, ttl=60.0, priority=0)
    cand = _sprint("mid", prio=5, req={"gpu_vram_gb": 8})
    victims = pol.select_yield_victims(cand, 5, led, {"big", "small"})
    assert [v.sprint_id for v in victims] == ["small"]


def test_a_lease_whose_release_frees_nothing_usable_is_not_hibernated(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, 10, 10)
    led.acquire("L0", {"gpu_vram_gb": 5}, now=0.0, ttl=60.0, priority=0)
    led.acquire("L0b", {"gpu_vram_gb": 5}, now=1.0, ttl=60.0, priority=10, preemptible=False)
    led.acquire("L1", {"gpu_vram_gb": 10}, now=2.0, ttl=60.0, priority=1)
    assert [l.gpu_devices for l in led.all_leases()] == [[0], [0], [1]]
    cand = _sprint("whole", prio=5, req={"gpu": 1})
    victims = pol.select_yield_victims(cand, 5, led, {"L0", "L0b", "L1"})
    assert [v.sprint_id for v in victims] == ["L1"]
