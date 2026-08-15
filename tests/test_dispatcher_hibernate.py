"""Cooperative preemption: the dispatcher never hard-kills. It yields a sprint's
lease only at a safe point (no running agent, no live job) by hibernating it, and
wakes hibernated sprints only from free capacity."""
from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _dispatcher(substrate, capacity, agent=None):
    return Dispatcher(substrate, agent or FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def _lease(disp, sid, req, prio=0, preemptible=True, now=0.0):
    disp.ledger.acquire(sid, req, now=now, ttl=3600.0, priority=prio, preemptible=preemptible)


def test_running_agent_is_not_yielded(substrate):
    # V's agent is actively running -> a higher-priority H cannot preempt it; H waits.
    agent = FakeAgent(linger=10**6, finished=False)
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.QUEUED, goals="g",
                                 plan=["long"], resources_required={"gpu": 1.0}, priority=0))
    disp = _dispatcher(substrate, {"gpu": 1.0}, agent)
    disp.run_one_cycle(now=0.0)                       # V granted + agent launched
    token = substrate.load_progress("V").agent_token
    substrate.save_sprint(Sprint(id="H", status=SprintStatus.QUEUED, goals="g",
                                 plan=["go"], resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)                       # H cannot evict a running agent
    disp.ledger.load()
    assert disp.ledger.lease_for("V") is not None
    assert disp.ledger.lease_for("H") is None         # H waits
    assert token not in agent.stopped                 # V never killed
    assert substrate.load_sprint("V").status == SprintStatus.EXECUTING


def test_sleeping_on_live_job_is_not_yielded(substrate):
    # V sleeps on a LIVE detached job -> protected; H waits for the job to finish.
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.EXECUTING, goals="g",
                                 resources_required={"gpu": 1.0}, priority=0))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    _lease(disp, "V", {"gpu": 1.0})
    prog = substrate.load_progress("V")
    prog.job_token = "1:1"
    substrate.save_progress(prog)
    disp.worker._job_alive = lambda t: True           # job still running
    substrate.save_sprint(Sprint(id="H", status=SprintStatus.QUEUED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("V") is not None      # kept
    assert disp.ledger.lease_for("H") is None          # H waits for the job
    assert substrate.load_sprint("V").status == SprintStatus.EXECUTING


def test_finished_job_sprint_hibernates_for_higher_priority(substrate):
    # V's job has finished (safe point) + H starved -> V hibernates, H runs next cycle.
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.EXECUTING, goals="g",
                                 resources_required={"gpu": 1.0}, priority=0))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    _lease(disp, "V", {"gpu": 1.0})
    prog = substrate.load_progress("V")
    prog.job_token = "1:1"
    prog.job_out = "j.out"
    substrate.save_progress(prog)
    disp.worker._job_alive = lambda t: False           # job finished
    substrate.save_sprint(Sprint(id="H", status=SprintStatus.QUEUED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)                         # V yields (hibernates)
    disp.ledger.load()
    assert substrate.load_sprint("V").status == SprintStatus.HIBERNATED
    assert disp.ledger.lease_for("V") is None           # lease released
    assert substrate.load_progress("V").assess_reason == "finished"  # assess context kept
    disp.run_one_cycle(now=2.0)                         # freed capacity -> H granted
    disp.ledger.load()
    assert disp.ledger.lease_for("H") is not None
    assert substrate.load_sprint("H").status == SprintStatus.EXECUTING


def test_uncollected_finished_agent_is_not_yielded(substrate):
    # Regression: after an agent exits (finished.json written) but BEFORE the beat
    # collects it, agent_token is still set -> NOT a safe point. A starved H must
    # not hibernate V there (which would discard the result); V is collected -> DONE.
    agent = FakeAgent(linger=0)            # V's agent exits at once, writing finished.json
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.QUEUED, goals="g",
                                 plan=["x"], resources_required={"gpu": 1.0}, priority=0))
    disp = _dispatcher(substrate, {"gpu": 1.0}, agent)
    disp.run_one_cycle(now=0.0)            # grant V + launch agent (token set, finished.json on disk, not running)
    assert substrate.load_progress("V").agent_token       # launched, not yet collected
    substrate.save_sprint(Sprint(id="H", status=SprintStatus.QUEUED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)            # must collect V, NOT hibernate it
    assert substrate.load_sprint("V").status == SprintStatus.DONE


def test_non_preemptible_is_never_hibernated(substrate):
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.EXECUTING, goals="g",
                                 resources_required={"gpu": 1.0}, priority=0, preemptible=False))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    _lease(disp, "V", {"gpu": 1.0}, preemptible=False)
    prog = substrate.load_progress("V")
    prog.job_token = "1:1"
    substrate.save_progress(prog)
    disp.worker._job_alive = lambda t: False           # even at a safe point
    substrate.save_sprint(Sprint(id="H", status=SprintStatus.QUEUED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert substrate.load_sprint("V").status == SprintStatus.EXECUTING   # protected
    assert disp.ledger.lease_for("H") is None


def test_hibernated_wakes_from_free_capacity(substrate):
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.HIBERNATED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=0))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("V") is not None
    assert substrate.load_sprint("V").status == SprintStatus.EXECUTING


def test_hibernated_does_not_preempt_others(substrate):
    # A HIBERNATED sprint never triggers a yield, even at higher priority — it only
    # re-enters from free capacity. No hibernate ping-pong.
    substrate.save_sprint(Sprint(id="L", status=SprintStatus.EXECUTING, goals="g",
                                 resources_required={"gpu": 1.0}, priority=0))
    disp = _dispatcher(substrate, {"gpu": 1.0})
    _lease(disp, "L", {"gpu": 1.0})                     # L holds the slot at a safe point
    substrate.save_sprint(Sprint(id="V", status=SprintStatus.HIBERNATED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("L") is not None       # L not yielded
    assert substrate.load_sprint("V").status == SprintStatus.HIBERNATED  # V still waits


def test_same_priority_no_thrash_under_aging(substrate):
    """Two sprints with equal base priority and queue time must never hibernate each
    other, even after multiple aging ticks. The lease's priority must stay current.

    Models the real failure: A is executing but its agent exited (usage limit) so it
    sits yieldable (no agent_token, no job_token). B is QUEUED at the same base
    priority. Without the fix, every aging tick (300s) makes B's effective priority
    exceed A's stale lease priority, triggering a pointless hibernate→re-grant."""
    substrate.save_sprint(Sprint(id="A", status=SprintStatus.EXECUTING, goals="g",
                                 resources_required={"gpu": 1.0}, priority=1))
    substrate.save_sprint(Sprint(id="B", status=SprintStatus.QUEUED, goals="g",
                                 resources_required={"gpu": 1.0}, priority=1))
    policy = SchedulerPolicy(aging_interval=300.0)
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool({"gpu": 1.0}), policy)
    queue_time = 1000.0
    disp._save_queue({"A": queue_time, "B": queue_time})
    _lease(disp, "A", {"gpu": 1.0}, prio=1, now=queue_time)
    # Block agent relaunch so A sits yieldable (empty agent_token) across cycles
    disp.worker._usage_gate = lambda: False
    for tick in range(1, 11):
        t = queue_time + tick * 300.0
        disp.run_one_cycle(now=t)
    assert substrate.load_sprint("A").status == SprintStatus.EXECUTING
    disp.ledger.load()
    assert disp.ledger.lease_for("A") is not None


def test_usage_blocked_skips_grants(substrate):
    """When the usage gate says no, the grant step must not start new work —
    identical to the pause guard. A QUEUED sprint stays queued."""
    substrate.save_sprint(Sprint(id="S", status=SprintStatus.QUEUED, goals="g",
                                 plan=["work"], resources_required={"gpu": 1.0}))
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0),
                      usage_gate=lambda: False)
    report = disp.run_one_cycle(now=0.0)
    assert report.granted == 0
    disp.ledger.load()
    assert disp.ledger.lease_for("S") is None
    assert substrate.load_sprint("S").status == SprintStatus.QUEUED


def test_usage_blocked_still_adopts_running_agent(substrate):
    """Even when usage is blocked, a sprint whose agent is physically running must
    be re-adopted (liveness), just like under pause — so the beat can collect its
    result instead of killing it."""
    agent = FakeAgent(linger=10**6, finished=False)
    substrate.save_sprint(Sprint(id="S", status=SprintStatus.QUEUED, goals="g",
                                 plan=["work"], resources_required={"gpu": 1.0}))
    # First: grant + launch under permissive usage
    disp = Dispatcher(substrate, agent, ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0),
                      usage_gate=lambda: True)
    disp.run_one_cycle(now=0.0)
    assert substrate.load_sprint("S").status == SprintStatus.EXECUTING
    token = substrate.load_progress("S").agent_token
    assert agent.is_running(token)
    # Expire the lease to simulate dispatcher outage
    disp.ledger.load()
    disp.ledger.expire(now=99999.0)
    disp.ledger.save()
    # Now usage is blocked — but the agent is still running
    disp.worker._usage_gate = lambda: False
    disp.run_one_cycle(now=100000.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("S") is not None         # re-adopted
    assert token not in agent.stopped                      # agent not killed
