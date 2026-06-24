from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _approved(sid, steps, req=None, prio=0):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=steps,
                  resources_required=req or {}, priority=prio)


def _dispatcher(substrate, capacity):
    return Dispatcher(substrate, ShellStepExecutor(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def test_runs_a_sprint_to_completion(substrate):
    substrate.save_sprint(_approved("sp1", [Step("s1", "true"), Step("s2", "true")],
                                    req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(6):
        disp.run_one_cycle(now=float(t))
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE


def test_never_over_allocates_under_contention(substrate):
    # 3 sprints each need the single GPU; capacity must never be exceeded.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_approved(sid, [Step("s1", "true")], req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(30):
        disp.run_one_cycle(now=float(t))
        # invariant after each cycle: never more than capacity leased
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0
    for sid in ("a", "b", "c"):
        assert substrate.load_sprint(sid).status == SprintStatus.DONE


def test_higher_priority_runs_first(substrate):
    substrate.save_sprint(_approved("lo", [Step("s1", "true")], req={"gpu": 1.0}, prio=0))
    substrate.save_sprint(_approved("hi", [Step("s1", "true")], req={"gpu": 1.0}, prio=9))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.run_one_cycle(now=0.0)  # grants the single gpu to the higher priority
    disp.ledger.load()
    assert disp.ledger.lease_for("hi") is not None
    assert disp.ledger.lease_for("lo") is None


def test_completion_releases_lease(substrate):
    substrate.save_sprint(_approved("sp1", [Step("s1", "true")], req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    for t in range(4):
        disp.run_one_cycle(now=float(t))
    disp.ledger.load()
    assert disp.ledger.all_leases() == []


def test_concurrent_when_capacity_allows(substrate):
    substrate.save_sprint(_approved("a", [Step("s1", "true")], req={"gpu": 1.0}))
    substrate.save_sprint(_approved("b", [Step("s1", "true")], req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 2.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("a") is not None
    assert disp.ledger.lease_for("b") is not None
