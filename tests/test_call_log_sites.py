"""Every Claude call site opens and closes a call.

Before this the PM and worker wrote a single end-only row and the wiki and chat
wrote nothing, so the Compute totals described two of five spenders and no killed
run appeared at all. These tests pin the remaining sites; the wiki has its own
file."""

from __future__ import annotations

from coscience import usage_meter
from coscience.models import Program, Sprint, SprintStatus


# --- worker ------------------------------------------------------------------

def _sprint(substrate, sid="p1-c0-x", pid="p1"):
    substrate.save_program(Program(id=pid, title="P", goals="g"))
    s = Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", program=pid,
               plan=["do the thing"], model="claude-sonnet-5")
    substrate.save_sprint(s)
    return s


def test_worker_opens_a_call_when_it_launches_an_agent(substrate, agent):
    from coscience.worker import Worker
    _sprint(substrate)
    Worker(substrate, agent).run_one_beat()

    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["kind"] == "worker"
    assert call["sprint"] == "p1-c0-x"
    assert call["program"] == "p1"          # was unreachable from a worker row before
    assert call["status"] == "running"


def test_worker_closes_the_call_when_the_agent_is_collected(substrate, agent):
    from coscience.worker import Worker
    _sprint(substrate)
    w = Worker(substrate, agent)
    w.run_one_beat()                        # launch
    w.run_one_beat()                        # collect (the fake finishes at once)

    calls = usage_meter.calls(substrate.repo_root)
    assert len(calls) == 1, "launch and collect must fold into ONE row"
    assert calls[0]["status"] in ("ok", "failed")
    assert calls[0]["ended_at"] is not None


def test_a_worker_agent_that_never_returns_is_lost_not_absent(substrate, agent):
    """The property the end-only row could not express."""
    from coscience.worker import Worker
    agent.linger = 10_000
    _sprint(substrate)
    Worker(substrate, agent).run_one_beat()

    (call,) = usage_meter.calls(substrate.repo_root, now=10**12)
    assert call["status"] == "lost"


# --- pm ----------------------------------------------------------------------

def test_pm_reasoning_is_one_call_with_program_set(substrate):
    from coscience.pm_agent import pm_beat
    from tests.test_pm_beat import _out
    from coscience.pm_reasoner import FakeReasoner
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    pm_beat(substrate, "p1", FakeReasoner([_out("a", "report-0")]))

    calls = [c for c in usage_meter.calls(substrate.repo_root) if c["kind"] == "pm"]
    assert len(calls) == 1
    assert calls[0]["program"] == "p1" and calls[0]["sprint"] == ""
    assert calls[0]["started_at"] is not None and calls[0]["ended_at"] is not None


def test_a_pm_call_that_raises_is_recorded_as_failed(substrate):
    from coscience.pm_agent import pm_beat
    substrate.save_program(Program(id="p1", title="P", goals="g"))

    class Boom:
        model = "claude-opus-5"

        def run(self, context):
            raise RuntimeError("malformed json")

    try:
        pm_beat(substrate, "p1", Boom())
    except Exception:
        pass

    calls = [c for c in usage_meter.calls(substrate.repo_root) if c["kind"] == "pm"]
    assert calls and calls[0]["status"] == "failed"
    assert calls[0]["started_at"] is not None, "a raising call still opened one"


# --- chat --------------------------------------------------------------------

def test_a_chat_turn_opens_and_closes_a_call(substrate):
    """Guidance threads are a real Claude spender that recorded nothing at all —
    the p2 thread on 09-01 cost real money and never reached the ledger."""
    from coscience.service import Service
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    svc = Service(substrate.repo_root)
    thread = svc.create_chat("p1", title="t")

    launched = {}

    def fake_launch(**kw):
        launched.update(kw)
        return "tok:1"

    svc.post_chat_message("p1", thread["id"], "hello", launch=fake_launch)

    (call,) = usage_meter.calls(substrate.repo_root, now=10.0)
    assert call["kind"] == "chat" and call["program"] == "p1"
    assert call["status"] == "running"

    tdir = substrate.chat_thread_dir("p1", thread["id"])
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "turn.out").write_text("")
    (tdir / "turn.exit").write_text("0")
    svc.get_chat_thread("p1", thread["id"])

    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["status"] == "ok" and call["ended_at"] is not None
