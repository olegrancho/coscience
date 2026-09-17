"""O8 Task 3: the PM sees pm-level escalations as their own thing and answers them."""
import json

from coscience import escalation
from coscience.models import Program, ProgressState, Sprint, SprintStatus
from coscience.pm_agent import _context_payload, gather_context, pm_beat
from coscience.pm_claude import parse_response, render_prompt
from coscience.pm_reasoner import FakeReasoner, PMContext, PMCycleOutput

RECORD = {"what": "gpu1 refuses SSH since 13:10", "tried": "three retries",
          "may_have_broken_something": False, "needs": "a working host",
          "host": "gpu1", "by": "agent"}


def _hosts_yaml(substrate):
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(
        "hosts:\n"
        "  local:\n    capacity: {cpu: 8}\n"
        "  gpu1:\n    ssh: gpu1\n    capacity: {cpu: 16}\n")


def _escalate(substrate, sid="p1-s1", program="p1", pm_answered=False, host="gpu1",
             title="Do the assay", record=None):
    sp = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["a"],
               program=program, title=title)
    substrate.save_sprint(sp)
    progress = ProgressState(sprint_id=sid, host=host, pm_answered=pm_answered)
    substrate.save_progress(progress)
    sp = substrate.load_sprint(sid)
    progress = substrate.load_progress(sid)
    escalation.raise_escalation(substrate, sp, progress, record or RECORD, now=100.0)
    return sid


def test_an_escalated_sprint_reaches_the_pm_as_an_escalation_not_as_feedback(
        substrate, every_host_placeable):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    _hosts_yaml(substrate)
    sid = _escalate(substrate)
    thread_id = substrate.load_sprint(sid).threads[-1]["id"]

    ctx = gather_context(substrate, "p1")

    assert ctx.escalations == [{
        "sprint_id": sid, "title": "Do the assay", "thread_id": thread_id,
        "by": "agent", "host": "gpu1", "what": "gpu1 refuses SSH since 13:10",
        "tried": "three retries", "may_have_broken_something": False,
        "needs": "a working host", "hosts_allowed": ["local"],
    }]
    assert not any(f.get("thread_id") == thread_id for f in ctx.sprint_feedback)


def test_a_human_level_escalation_is_not_shown_to_the_pm(substrate, every_host_placeable):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    _hosts_yaml(substrate)
    _escalate(substrate, pm_answered=True)   # -> level "human" (see raise_escalation)

    ctx = gather_context(substrate, "p1")

    assert ctx.escalations == []


def test_a_pm_level_escalation_with_a_pending_stop_is_not_shown_to_the_pm(
        substrate, every_host_placeable):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    _hosts_yaml(substrate)
    sid = _escalate(substrate)
    from coscience import escalation
    assert escalation.answer(substrate, sid, "stop", by="oleg") == ""

    ctx = gather_context(substrate, "p1")

    assert ctx.escalations == []


def test_escalations_change_the_fingerprint_only_when_present():
    bare = PMContext(program_id="p1", goals="g", cycle=0)
    assert "escalations" not in _context_payload(bare)

    with_one = PMContext(program_id="p1", goals="g", cycle=0, escalations=[
        {"sprint_id": "s1", "thread_id": "t1", "title": "", "by": "", "host": "",
         "what": "", "tried": "", "may_have_broken_something": False, "needs": "",
         "hosts_allowed": []}])
    assert _context_payload(with_one)["escalations"] == [("s1", "t1")]


def test_the_prompt_lists_escalations_and_the_answer_field():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, escalations=[{
        "sprint_id": "p1-s1", "title": "Do the assay", "thread_id": "t1",
        "by": "agent", "host": "gpu1", "what": "gpu1 refuses SSH",
        "tried": "three retries", "may_have_broken_something": True,
        "needs": "a working host", "hosts_allowed": ["local"],
    }])
    p = render_prompt(ctx)
    assert "p1-s1" in p
    assert "ESCALATIONS" in p
    assert '"escalation_answers"' in p
    assert "resume" in p and "reallocate" in p and "to_human" in p
    # the answer shape asks the PM to echo the thread_id back (Fix D)
    assert '"escalation_answers": [{"sprint_id"' in p
    block = p.split('"escalation_answers": [{"sprint_id"')[1].split('"edge_ops"')[0]
    assert '"thread_id"' in block
    assert ("Choose to_human whenever the issue needs someone on a machine, the "
            "program's data may be damaged, or you are not confident it can be "
            "fixed easily") in p


def test_escalation_answers_are_parsed():
    text = json.dumps({"report": "r", "escalation_answers": [
        {"sprint_id": "s1", "action": "resume", "instructions": "wait", "thread_id": "t1"},
        {"sprint_id": "", "action": "resume"},           # missing sprint_id -> dropped
        {"sprint_id": "s2"},                              # missing action -> dropped
        "not-a-dict",                                     # non-dict -> dropped
    ]})
    out = parse_response(text)
    assert out.escalation_answers == [
        {"sprint_id": "s1", "action": "resume", "instructions": "wait", "host": "",
         "thread_id": "t1"}]


def test_escalation_answers_default_thread_id_to_empty():
    text = json.dumps({"report": "r", "escalation_answers": [
        {"sprint_id": "s1", "action": "resume", "instructions": "wait"}]})
    out = parse_response(text)
    assert out.escalation_answers[0]["thread_id"] == ""


def test_applying_answers_moves_the_sprint_and_reports_skips(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    sid = _escalate(substrate)
    out = PMCycleOutput(report="r", escalation_answers=[
        {"sprint_id": sid, "action": "resume", "instructions": "wait", "host": ""},
        {"sprint_id": "s9", "action": "resume", "instructions": "", "host": ""},
    ])
    summary = pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    assert substrate.load_sprint(sid).status == SprintStatus.EXECUTING
    assert substrate.load_progress(sid).resume_note == "wait"
    assert summary["escalations_answered"] == [(sid, "resume")]
    assert summary["escalation_skipped"] == [{"id": "s9", "why": "no such sprint"}]
    report = substrate.load_report("p1")
    assert f"Escalation answered: `{sid}` (resume)" in report
    assert "Escalation answer FAILED: `s9` — no such sprint" in report


def test_applying_an_answer_with_a_stale_thread_id_is_skipped(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    sid = _escalate(substrate)
    out = PMCycleOutput(report="r", escalation_answers=[
        {"sprint_id": sid, "action": "resume", "instructions": "wait", "host": "",
         "thread_id": "not-the-real-one"}])
    summary = pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    assert substrate.load_sprint(sid).status == SprintStatus.ESCALATED   # untouched
    assert summary["escalations_answered"] == []
    assert summary["escalation_skipped"] == [
        {"id": sid, "why": "this answer is for an earlier escalation"}]


def test_the_pm_cannot_answer_another_programs_escalation(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    substrate.save_program(Program(id="p2", title="P2", goals="g"))
    sid = _escalate(substrate, sid="p2-s1", program="p2")

    out = PMCycleOutput(report="r", escalation_answers=[
        {"sprint_id": sid, "action": "resume", "instructions": "wait", "host": ""}])
    summary = pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    assert summary["escalations_answered"] == []
    assert summary["escalation_skipped"] == [{"id": sid, "why": "belongs to program p2"}]
    # untouched: still escalated, not resumed by the wrong program's PM
    assert substrate.load_sprint(sid).status == SprintStatus.ESCALATED
