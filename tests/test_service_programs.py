import json

import pytest

from coscience.models import Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import NotFoundError, Service
from coscience.pm_runner import pm_run_once
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def test_pm_chat_appends_and_persists(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="cure",
                                       status=ProgramStatus.ACTIVE))

    seen = {}
    def fake_chat(context, history, message):
        seen["goals"] = context.goals
        seen["history_len"] = len(history)          # prior turns, excluding this message
        return f"answer to: {message}"

    out = svc.chat("p1", "what's next?", chat_fn=fake_chat)
    assert out["reply"] == "answer to: what's next?"
    assert seen["goals"] == "cure" and seen["history_len"] == 0
    msgs = svc.list_chat("p1")
    assert [m["role"] for m in msgs] == ["user", "pm"]
    assert msgs[0]["text"] == "what's next?"
    svc.chat("p1", "and after that?", chat_fn=fake_chat)   # follow-up sees prior turns
    assert seen["history_len"] == 2
    assert len(svc.list_chat("p1")) == 4


def test_pm_chat_rejects_empty(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g"))
    with pytest.raises(ValueError):
        svc.chat("p1", "   ", chat_fn=lambda *a: "x")


def test_list_and_get_program(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="Cancer", goals="cure"))
    svc.substrate.save_sprint(Sprint(id="p1-s1", status=SprintStatus.PROPOSED,
                                     goals="assay", plan=["do it"], program="p1"))
    assert svc.list_programs() == [{"id": "p1", "title": "Cancer",
                                    "status": "active", "goals": "cure"}]
    detail = svc.get_program("p1")
    assert detail["goals"] == "cure"
    assert detail["cycle"] == 0
    assert [s["id"] for s in detail["sprints"]] == ["p1-s1"]
    json.dumps(detail)  # JSON-serialisable


def test_list_programs_status_filter(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="a", title="A", goals="x"))
    svc.substrate.save_program(Program(id="b", title="B", goals="y",
                                       status=ProgramStatus.CLOSED))
    assert [p["id"] for p in svc.list_programs(status="active")] == ["a"]


def test_get_missing_program_raises(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_program("nope")


def test_set_program_status_pause_resume(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    svc.set_program_status("p1", "paused")
    assert svc.get_program("p1")["status"] == "paused"
    svc.set_program_status("p1", "active")
    assert svc.get_program("p1")["status"] == "active"


def test_set_program_status_invalid_raises(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    with pytest.raises(ValueError):
        svc.set_program_status("p1", "bogus")


def test_set_program_status_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).set_program_status("nope", "paused")


def test_paused_program_is_skipped_by_pm(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    svc.set_program_status("p1", "paused")
    reasoner = FakeReasoner([PMCycleOutput(
        proposals=[ProposedSprint(suffix="x", goals="go", plan=["do it"])])])
    summaries = pm_run_once(svc.substrate, reasoner)
    assert summaries == []           # paused program not beaten
    assert reasoner.calls == []      # reasoner never consulted
