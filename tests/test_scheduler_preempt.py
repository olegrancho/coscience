"""Yield-victim selection: which leases to hibernate so a starved candidate can
run. Only leases the dispatcher marks yieldable (safe point) are considered."""
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio)


def _ledger(tmp_path, capacity):
    led = Ledger(ResourcePool(capacity), tmp_path / "leases.json")
    led.load()
    return led


def _all_ids(led):
    return {l.sprint_id for l in led.all_leases()}


def test_no_yield_when_it_already_fits(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 2.0})
    led.acquire("held", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_yield_victims(cand, 5, led, _all_ids(led)) == []


def test_yields_lower_priority_holder(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("lo", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0, preemptible=True)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    victims = pol.select_yield_victims(cand, 5, led, _all_ids(led))
    assert [v.sprint_id for v in victims] == ["lo"]


def test_will_not_yield_equal_or_higher_priority(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("peer", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=5, preemptible=True)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_yield_victims(cand, 5, led, _all_ids(led)) == []


def test_will_not_yield_non_preemptible(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("lo", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0, preemptible=False)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_yield_victims(cand, 5, led, _all_ids(led)) == []


def test_will_not_yield_a_lease_not_at_a_safe_point(tmp_path):
    # A lease absent from the yieldable set (running agent / live job) is protected.
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    led.acquire("lo", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0, preemptible=True)
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    assert pol.select_yield_victims(cand, 5, led, yieldable_ids=set()) == []


def test_minimal_victim_set(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 3.0})
    led.acquire("a", {"gpu": 1.0}, now=0.0, ttl=60.0, priority=0)
    led.acquire("b", {"gpu": 1.0}, now=1.0, ttl=60.0, priority=1)
    led.acquire("c", {"gpu": 1.0}, now=2.0, ttl=60.0, priority=0)
    # capacity 3, all held -> available 0. candidate needs 1.
    # eligible (priority<5): a,b,c. lowest priority first: a(0)&c(0) before b(1);
    # tie on priority broken by larger granted_at first -> c (granted 2.0) before a.
    cand = _sprint("hi", prio=5, req={"gpu": 1.0})
    victims = pol.select_yield_victims(cand, 5, led, _all_ids(led))
    assert [v.sprint_id for v in victims] == ["c"]
