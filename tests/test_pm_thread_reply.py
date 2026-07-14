from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience import pm_agent, threads


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_pm_reply_appended_to_thread(substrate):
    _prog(substrate)
    s = Sprint(id="p1-c0-x", status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1")
    th = threads.new_thread("pm", "change to cpu", "stroganov", now=1.0)
    s.threads.append(th)
    substrate.save_sprint(s)

    ctx = pm_agent.gather_context(substrate, "p1")
    fb = [f for f in ctx.sprint_feedback if f["sprint_id"] == "p1-c0-x"]
    assert fb and fb[0]["thread_id"] == th["id"]      # surfaced to the PM
    assert fb[0]["messages"][-1] == {"role": "human", "text": "change to cpu"}

    out = PMCycleOutput(report="r",
                        thread_replies=[{"thread_id": th["id"], "text": "done — set cpu"}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    got = substrate.load_sprint("p1-c0-x").threads[0]
    assert got["messages"][-1]["role"] == "pm"
    assert got["messages"][-1]["text"] == "done — set cpu"
    assert got["agent_unseen"] is True

    # Now closed to further PM replies until the human speaks again.
    ctx2 = pm_agent.gather_context(substrate, "p1")
    assert not [f for f in ctx2.sprint_feedback if f["sprint_id"] == "p1-c0-x"]


def test_pm_reply_ignores_unmatched_or_answered_thread(substrate):
    # A reply naming a thread id that isn't open/human-last on any of this
    # program's sprints must be silently dropped, not raise.
    _prog(substrate)
    s = Sprint(id="p1-c0-y", status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1")
    substrate.save_sprint(s)
    out = PMCycleOutput(report="r", thread_replies=[{"thread_id": "nope", "text": "ignored"}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    assert substrate.load_sprint("p1-c0-y").threads == []
