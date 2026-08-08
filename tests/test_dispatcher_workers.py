"""End to end: a worker cap bounds how many sprints a real cycle leases."""
from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import WORKER_KEY, ResourcePool
from coscience.scheduler import SchedulerPolicy


def _queued(sid, req=None, prio=0):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["do the work"],
                  resources_required=req or {}, priority=prio)


def _dispatcher(substrate, capacity):
    return Dispatcher(substrate, FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def test_one_cycle_grants_only_one_sprint_under_a_worker_cap(substrate):
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid, req={"cpu": 1.0}))
    disp = _dispatcher(substrate, {"cpu": 16.0, WORKER_KEY: 1.0})
    report = disp.run_one_cycle(now=0.0)
    assert report.granted == 1
    assert report.waiting == 2


def test_sprints_declaring_nothing_are_bounded_too(substrate):
    # Without a worker cap these are all granted: all() over {} is vacuously true.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = _dispatcher(substrate, {WORKER_KEY: 1.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert len(disp.ledger.all_leases()) == 1


def test_the_cap_holds_across_cycles(substrate):
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = _dispatcher(substrate, {WORKER_KEY: 1.0})
    for t in range(30):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get(WORKER_KEY, 0.0) <= 1.0


def test_all_sprints_still_finish_under_a_cap_of_one(substrate):
    # Serialised, not starved: the cap orders work, it doesn't drop it.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = _dispatcher(substrate, {WORKER_KEY: 1.0})
    for t in range(60):
        disp.run_one_cycle(now=float(t))
    for sid in ("a", "b", "c"):
        assert substrate.load_sprint(sid).status == SprintStatus.DONE


def test_two_workers_run_two_sprints_at_once(substrate):
    substrate.save_sprint(_queued("a"))
    substrate.save_sprint(_queued("b"))
    disp = _dispatcher(substrate, {WORKER_KEY: 2.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("a") is not None
    assert disp.ledger.lease_for("b") is not None
