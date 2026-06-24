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


def test_effective_priority_ages_up():
    pol = SchedulerPolicy(aging_interval=10.0)
    s = _sprint("sp1", prio=1)
    assert pol.effective_priority(s, queued_at=0.0, now=0.0) == 1
    assert pol.effective_priority(s, queued_at=0.0, now=25.0) == 3  # 1 + 25//10


def test_grants_respect_capacity_and_priority(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 1.0})
    lo = _sprint("lo", prio=0, req={"gpu": 1.0})
    hi = _sprint("hi", prio=5, req={"gpu": 1.0})
    q = {"lo": 0.0, "hi": 0.0}
    granted = pol.select_grants([lo, hi], q, led, now=0.0)
    assert [s.id for s in granted] == ["hi"]  # only one gpu, higher priority wins


def test_grants_fifo_tiebreak_on_equal_priority(tmp_path):
    pol = SchedulerPolicy(aging_interval=0.0)  # disable aging for a clean FIFO check
    led = _ledger(tmp_path, {"gpu": 1.0})
    a = _sprint("a", prio=0, req={"gpu": 1.0})
    b = _sprint("b", prio=0, req={"gpu": 1.0})
    q = {"a": 10.0, "b": 5.0}  # b queued earlier
    granted = pol.select_grants([a, b], q, led, now=20.0)
    assert [s.id for s in granted] == ["b"]


def test_grants_multiple_when_capacity_allows(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 2.0})
    a = _sprint("a", req={"gpu": 1.0})
    b = _sprint("b", req={"gpu": 1.0})
    q = {"a": 0.0, "b": 0.0}
    granted = pol.select_grants([a, b], q, led, now=0.0)
    assert {s.id for s in granted} == {"a", "b"}


def test_no_resource_sprints_always_granted(tmp_path):
    pol = SchedulerPolicy()
    led = _ledger(tmp_path, {"gpu": 0.0})
    a = _sprint("a", req={})
    granted = pol.select_grants([a], {"a": 0.0}, led, now=0.0)
    assert [s.id for s in granted] == ["a"]
