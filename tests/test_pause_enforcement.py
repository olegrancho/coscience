"""A paused platform starts nothing new, but its bookkeeping keeps running — that is
what lets already-running work drain instead of stranding its lease."""
from tests.conftest import FakeAgent

from coscience import pause
from coscience import pm_claude as pm_claude_mod
from coscience import worker as worker_mod
from coscience.dispatcher import Dispatcher
from coscience.models import Program, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service


def _queued(sid, req=None):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["do the work"],
                  resources_required=req or {})


def _dispatcher(substrate, capacity, agent=None):
    return Dispatcher(substrate, agent or FakeAgent(), ResourcePool(capacity),
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


def test_a_leaseless_but_running_sprint_is_readopted_while_paused(substrate, monkeypatch):
    """The grant loop has a second job besides starting new work: re-adopting a sprint
    that is still physically running but lost its lease (the dispatch loop was down
    past the 3600s TTL). Skipping the whole loop while paused hands that sprint to the
    reconcile step, which kills the live agent — the one thing pause promises never to
    do. Liveness is the exception; a QUEUED sprint has no agent and still waits."""
    _pause_only_gate(monkeypatch, substrate.repo_root)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))
    agent = FakeAgent(linger=50)                     # its agent keeps running throughout
    disp = _dispatcher(substrate, {"gpu": 2.0}, agent=agent)

    disp.run_one_cycle(now=0.0)                      # grant + launch
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert agent.started == ["sp1"]

    pause.set_paused(substrate.repo_root, True)
    substrate.save_sprint(_queued("sp2", req={"gpu": 1.0}))   # a fresh candidate, fits

    disp.run_one_cycle(now=4000.0)                   # past the TTL: sp1's lease expires

    assert agent.stopped == []                       # the in-flight session survives
    assert disp.ledger.lease_for("sp1") is not None  # ...because it was re-adopted
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert agent.started == ["sp1"]                  # and no second launch
    # Starts nothing new stays exact: sp2 has no agent and no job, so it waits.
    assert substrate.load_sprint("sp2").status == SprintStatus.QUEUED
    assert disp.ledger.lease_for("sp2") is None


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


# --- what the human is told -------------------------------------------------
# Every human-triggered Claude path is gated on the same usage_ok, so a paused
# platform reported itself as "usage exhausted; it will resume after the reset".
# That points at a reset which changes nothing — only Resume does.

def _paused_service(substrate, monkeypatch):
    """A service on a paused substrate, with a reasoner that explodes if built —
    nothing may reach Claude from here."""
    _pause_only_gate(monkeypatch, substrate.repo_root)
    substrate.save_program(Program(id="p", title="P", goals="g"))
    pause.set_paused(substrate.repo_root, True)

    class _NoReasoner:
        def __init__(self, *a, **k):
            raise AssertionError("no reasoner may be built while paused")
    monkeypatch.setattr(pm_claude_mod, "ClaudeCodeReasoner", _NoReasoner)
    return Service(substrate.repo_root)


def test_replan_reports_the_pause_not_an_exhausted_budget(substrate, monkeypatch):
    r = _paused_service(substrate, monkeypatch).replan("p")

    assert r.get("paused") is True
    assert r.get("throttled") is not True      # a reset would not clear this
    assert r.get("skipped") is True


def test_a_pm_directive_reports_the_pause_not_an_exhausted_budget(substrate, monkeypatch):
    r = _paused_service(substrate, monkeypatch).run_pm_directive("p", "brainstorm")

    assert r.get("paused") is True
    assert r.get("throttled") is not True
    assert r.get("skipped") is True


def test_chat_tells_the_human_to_resume_rather_than_wait_for_a_reset(substrate, monkeypatch):
    svc = _paused_service(substrate, monkeypatch)
    cid = svc.create_chat("p")["id"]

    thread = svc.post_chat_message("p", cid, "what next?")

    reply = thread["messages"][-1]
    assert reply["role"] == "pm"
    assert "Resume" in reply["text"]
    assert "usage is exhausted" not in reply["text"]
    assert thread["busy"] is False             # nothing was launched
