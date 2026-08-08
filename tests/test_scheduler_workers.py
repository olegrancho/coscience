"""The worker cap: `workers: N` in the pool bounds how many sprints hold leases."""
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import WORKER_KEY, ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio)


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def test_worker_cap_bounds_grants(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"cpu": 16.0, WORKER_KEY: 2.0})
    sprints = [_sprint(f"s{i}", req={"cpu": 1.0}) for i in range(5)]
    q = {s.id: 0.0 for s in sprints}
    granted = pol.select_grants(sprints, q, led, now=0.0)
    assert len(granted) == 2


def test_worker_cap_bounds_sprints_declaring_no_resources(tmp_path):
    # The hole this feature closes: all() over an empty dict is vacuously true,
    # so uncapped these would all be granted.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 1.0})
    sprints = [_sprint(f"s{i}", req={}) for i in range(4)]
    q = {s.id: 0.0 for s in sprints}
    granted = pol.select_grants(sprints, q, led, now=0.0)
    assert len(granted) == 1


def test_worker_cap_grants_by_priority(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 1.0})
    lo = _sprint("lo", prio=0)
    hi = _sprint("hi", prio=5)
    granted = pol.select_grants([lo, hi], {"lo": 0.0, "hi": 0.0}, led, now=0.0)
    assert [s.id for s in granted] == ["hi"]


def test_worker_cap_of_zero_grants_nothing(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"cpu": 16.0, WORKER_KEY: 0.0})
    granted = pol.select_grants([_sprint("s1", req={"cpu": 1.0})], {"s1": 0.0}, led, now=0.0)
    assert granted == []


def test_existing_leases_consume_the_cap(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 2.0})
    led.acquire("running", {WORKER_KEY: 1.0}, now=0.0, ttl=60.0)
    sprints = [_sprint("a"), _sprint("b")]
    granted = pol.select_grants(sprints, {"a": 0.0, "b": 0.0}, led, now=0.0)
    assert len(granted) == 1


def test_yield_victims_account_for_the_worker_slot(tmp_path):
    # The candidate needs a worker slot it can only get by preempting a holder.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {WORKER_KEY: 1.0})
    led.acquire("holder", {WORKER_KEY: 1.0}, now=0.0, ttl=60.0, priority=0)
    cand = _sprint("cand", prio=5)
    victims = pol.select_yield_victims(cand, 5, led, yieldable_ids={"holder"})
    assert [v.sprint_id for v in victims] == ["holder"]


def test_uncapped_pool_still_grants_everything(tmp_path):
    # Regression guard: no `workers` key means today's behaviour, unchanged.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"cpu": 2.0})
    sprints = [_sprint(f"s{i}", req={}) for i in range(6)]
    q = {s.id: 0.0 for s in sprints}
    assert len(pol.select_grants(sprints, q, led, now=0.0)) == 6
