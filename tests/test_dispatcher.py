from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _approved(sid, req=None, prio=0):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=["do the work"],
                  resources_required=req or {}, priority=prio)


def _dispatcher(substrate, capacity, agent=None):
    return Dispatcher(substrate, agent or FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def test_runs_a_sprint_to_completion(substrate):
    substrate.save_sprint(_approved("sp1", req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(6):
        disp.run_one_cycle(now=float(t))
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE


def test_never_over_allocates_under_contention(substrate):
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_approved(sid, req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(30):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0
    for sid in ("a", "b", "c"):
        assert substrate.load_sprint(sid).status == SprintStatus.DONE


def test_higher_priority_runs_first(substrate):
    substrate.save_sprint(_approved("lo", req={"gpu": 1.0}, prio=0))
    substrate.save_sprint(_approved("hi", req={"gpu": 1.0}, prio=9))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("hi") is not None
    assert disp.ledger.lease_for("lo") is None


def test_completion_releases_lease(substrate):
    substrate.save_sprint(_approved("sp1", req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(4):
        disp.run_one_cycle(now=float(t))
    disp.ledger.load()
    assert disp.ledger.all_leases() == []


def test_concurrent_when_capacity_allows(substrate):
    substrate.save_sprint(_approved("a", req={"gpu": 1.0}))
    substrate.save_sprint(_approved("b", req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 2.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("a") is not None
    assert disp.ledger.lease_for("b") is not None
