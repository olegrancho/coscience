"""O2: one cycle's grants and yields respect host boundaries and reservations."""
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _sprint(sid, prio=0, req=None, program=None):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=[],
                  resources_required=req or {}, priority=prio, program=program)


def _ledger(tmp_path, big_programs=None):
    big = {"ssh": "big", "capacity": {"cpu": 8}}
    if big_programs is not None:
        big["programs"] = big_programs
    led = Ledger(ResourcePool.from_dict({"cpu": 8, "hosts": {"big": big}}),
                 tmp_path / "leases.json")
    led.load()
    return led


def test_one_cycle_does_not_double_book_a_host(tmp_path, every_host_placeable):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path)
    sprints = [_sprint(s, req={"cpu": 8.0}) for s in ("a", "b", "c")]
    granted = pol.select_grants(sprints, {"a": 0.0, "b": 1.0, "c": 2.0}, led, now=0.0)
    assert [s.id for s in granted] == ["a", "b"]           # one per host; c waits


def test_a_request_split_across_hosts_is_not_granted(tmp_path, every_host_placeable):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path)
    granted = pol.select_grants([_sprint("wide", req={"cpu": 12.0})], {"wide": 0.0}, led, now=0.0)
    assert granted == []


def test_a_reserved_host_takes_no_grant_for_another_program(tmp_path, every_host_placeable):
    pol = SchedulerPolicy(aging_interval=0.0)
    led = _ledger(tmp_path, big_programs=["p2"])
    sprints = [_sprint("p5-a", req={"cpu": 8.0}, program="p5"),
               _sprint("p5-b", req={"cpu": 8.0}, program="p5")]
    granted = pol.select_grants(sprints, {"p5-a": 0.0, "p5-b": 1.0}, led, now=0.0)
    assert [s.id for s in granted] == ["p5-a"]


def test_victims_come_from_the_host_the_candidate_can_use(tmp_path, every_host_placeable):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, big_programs=["p2"])
    led.acquire("on-local", {"cpu": 8.0}, now=0.0, ttl=60.0, priority=0, program="p5")
    led.acquire("on-big", {"cpu": 8.0}, now=1.0, ttl=60.0, priority=0, program="p2")
    cand = _sprint("p5-hi", prio=5, req={"cpu": 8.0}, program="p5")
    # on-big was granted later, so a host-blind pick would take it first — but freeing
    # big does nothing for a p5 sprint.
    victims = pol.select_yield_victims(cand, 5, led, {"on-local", "on-big"})
    assert [v.sprint_id for v in victims] == ["on-local"]


def test_no_victims_when_the_only_usable_host_has_none_yieldable(tmp_path, every_host_placeable):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, big_programs=["p2"])
    led.acquire("on-local", {"cpu": 8.0}, now=0.0, ttl=60.0, priority=0, program="p5")
    led.acquire("on-big", {"cpu": 8.0}, now=1.0, ttl=60.0, priority=0, program="p2")
    cand = _sprint("p5-hi", prio=5, req={"cpu": 8.0}, program="p5")
    assert pol.select_yield_victims(cand, 5, led, {"on-big"}) == []
