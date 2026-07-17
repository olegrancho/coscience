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
