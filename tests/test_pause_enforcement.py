"""A paused platform starts nothing new, but its bookkeeping keeps running — that is
what lets already-running work drain instead of stranding its lease."""
from tests.conftest import FakeAgent

from coscience import pause
from coscience import worker as worker_mod
from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _queued(sid, req=None):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["do the work"],
                  resources_required=req or {})


def _dispatcher(substrate, capacity):
    return Dispatcher(substrate, FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def _pause_only_gate(monkeypatch, repo_root):
    """Usage always healthy; only the pause marker can refuse."""
    monkeypatch.setattr(worker_mod, "claude_usage_ok",
                        lambda *a, **k: not pause.is_paused(repo_root))


def test_the_workers_default_gate_is_given_the_substrate(substrate, monkeypatch):
    seen = {}
    monkeypatch.setattr(worker_mod, "claude_usage_ok",
                        lambda *a, **k: seen.update(k) or True)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))

    _dispatcher(substrate, {"gpu": 1.0}).run_one_cycle(now=0.0)

    assert seen.get("repo_root") == substrate.repo_root


def test_a_paused_dispatcher_grants_nothing_new(substrate, monkeypatch):
    _pause_only_gate(monkeypatch, substrate.repo_root)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))
    pause.set_paused(substrate.repo_root, True)
    disp = _dispatcher(substrate, {"gpu": 1.0})

    for t in range(4):
        disp.run_one_cycle(now=float(t))

    assert substrate.load_sprint("sp1").status == SprintStatus.QUEUED


def test_work_already_running_drains_while_paused(substrate, monkeypatch):
    """The point of choosing drain over hibernate: pausing mid-flight must not strand
    a lease. The collect path holds no usage gate, so a finished agent is still
    reaped, the sprint reaches DONE and the lease is released."""
    _pause_only_gate(monkeypatch, substrate.repo_root)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})

    disp.run_one_cycle(now=0.0)                                   # grant + launch
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    pause.set_paused(substrate.repo_root, True)                   # pause mid-flight

    for t in range(1, 6):
        disp.run_one_cycle(now=float(t))

    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    disp.ledger.load()
    assert disp.ledger.lease_for("sp1") is None
