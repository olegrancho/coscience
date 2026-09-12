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


def test_the_cap_holds_across_cycles_with_persistent_leases(substrate):
    # A lease that never expires on its own (linger=50, finished=False) is what
    # exposes the dispatcher's own effective_requirement call: if it stops
    # attaching WORKER_KEY, cycle 2+ keeps granting new sprints on top of the
    # still-held first one, since select_grants only screens sprints that are
    # still leaseless.
    for sid in ("a", "b", "c"):
        substrate.save_sprint(_queued(sid))
    disp = Dispatcher(substrate, FakeAgent(linger=50, finished=False),
                      ResourcePool({WORKER_KEY: 1.0}), SchedulerPolicy(aging_interval=0.0))
    for t in range(5):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert len(disp.ledger.all_leases()) <= 1


def test_a_granted_lease_records_one_worker_slot(substrate):
    substrate.save_sprint(_queued("a"))
    disp = Dispatcher(substrate, FakeAgent(linger=50, finished=False),
                      ResourcePool({WORKER_KEY: 1.0}), SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("a").amounts[WORKER_KEY] == 1.0


def test_a_sprint_asleep_on_a_job_does_not_block_the_next_one(substrate):
    """p5-c26 held the substrate's only worker slot for 15 hours while a detached
    GPU job trained and no agent process existed, so nothing else could be
    dispatched. The slot bounds agent processes; a sleeping sprint runs none.

    Its lease stays — the job really is using the cpu/gpu, and the dispatcher's
    no-lease-means-no-running-job reconcile would otherwise kill the job."""
    import time

    substrate.save_sprint(_queued("sleeper", req={"cpu": 2.0}))
    disp = _dispatcher(substrate, {"cpu": 16.0, WORKER_KEY: 1.0})
    disp.run_one_cycle(now=0.0)
    assert disp.ledger.lease_for("sleeper") is not None

    # Put it to sleep on a live job the way a real worker would, then beat it.
    prog = substrate.load_progress("sleeper")
    prog.agent_token = ""
    prog.job_token, prog.job_out, prog.job_note = "1:1", "j.out", "train"
    prog.job_started_at = time.time()
    prog.job_next_wake = time.time() + 9999
    prog.job_max_seconds = 9e9
    substrate.save_progress(prog)
    disp.worker._job_alive = lambda t: True

    substrate.save_sprint(_queued("newcomer", req={"cpu": 1.0}))
    # Grants are evaluated before beats within a cycle, so the slot the sleeper
    # hands back lands one cycle later — five seconds on the live loop.
    assert disp.run_one_cycle(now=1.0).granted == 0
    report = disp.run_one_cycle(now=2.0)

    assert report.granted == 1                                  # newcomer got in
    assert disp.ledger.lease_for("newcomer") is not None
    assert disp.ledger.lease_for("sleeper") is not None          # still holds its cpu
    assert WORKER_KEY not in disp.ledger.lease_for("sleeper").amounts
    assert disp.ledger.used()["cpu"] == 3.0                      # 2 sleeping + 1 new
    assert substrate.load_progress("sleeper").job_token == "1:1"  # job untouched
