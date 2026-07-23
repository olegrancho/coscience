from coscience import artifacts, threads
from coscience.models import Program, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _program(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))


def test_artifact_task_becomes_proposed_bound_sprint(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "tighten-intro", "artifact_ids": ["doc"], "create": [],
         "instructions": "Tighten the introduction."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    sid = "p-c0-tighten-intro"
    s = substrate.load_sprint(sid)
    assert s.status == SprintStatus.PROPOSED
    assert s.artifacts_bound == ["doc"]
    assert "Tighten" in s.goals
    assert s.title == "Update doc"          # derived when the PM omits a title


def test_artifact_task_honors_pm_title(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "tighten-intro", "title": "Tighten the intro",
         "artifact_ids": ["doc"], "create": [],
         "instructions": "Tighten the introduction."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    s = substrate.load_sprint("p-c0-tighten-intro")
    assert s.title == "Tighten the intro"


def test_artifact_task_create_new_artifact_sprint(substrate):
    _program(substrate)
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "write-manuscript", "artifact_ids": [],
         "create": [{"title": "Manuscript", "kind": "md"}],
         "instructions": "Write a manuscript from the results."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    s = substrate.load_sprint("p-c0-write-manuscript")
    assert s.artifacts_create and s.artifacts_create[0]["title"] == "Manuscript"
    assert s.artifacts_create[0]["kind"] == "md"
    assert s.artifacts_create[0]["aid"]      # a slug was assigned
    assert s.title == "Create: Manuscript"   # derived from what it creates


def test_pm_reply_lands_on_artifact_thread(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    a = substrate.load_artifact("p", "doc")
    th = threads.new_thread("pm", "please tighten", by="oleg", now=1.0)
    a.threads.append(th)
    substrate.save_artifact(a)
    out = PMCycleOutput(report="r",
                        thread_replies=[{"thread_id": th["id"], "text": "Proposed a sprint to do it."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=2.0, force=True)
    a2 = substrate.load_artifact("p", "doc")
    msgs = a2.threads[0]["messages"]
    assert msgs[-1]["role"] == "pm"
    assert "Proposed a sprint" in msgs[-1]["text"]
