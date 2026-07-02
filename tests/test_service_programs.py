import json

import pytest

from coscience.models import Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import NotFoundError, Service
from coscience.pm_runner import pm_run_once
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def _fake_launch(reply="stub reply", session="sess-1"):
    """A launch() stand-in: write a finished stream-json turn synchronously so the
    next poll collects it — no real claude call."""
    def launch(*, thread_dir, workdir, prompt, scope, session_id, resume, model):
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "turn.out").write_text(
            json.dumps({"type": "assistant"}) + "\n"
            + json.dumps({"type": "result", "result": f"{reply}: {prompt.splitlines()[-1]}",
                          "session_id": session, "total_cost_usd": 0.0, "usage": {}}) + "\n")
        (thread_dir / "turn.exit").write_text("0")
        return "fake:token"
    return launch


def test_chat_thread_flow(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="cure",
                                       status=ProgramStatus.ACTIVE))
    t = svc.create_chat("p1", "My chat")
    tid = t["id"]
    assert t["scope"] == "read" and t["title"] == "My chat"

    posted = svc.post_chat_message("p1", tid, "what's next?", launch=_fake_launch())
    assert posted["busy"] is True and posted["messages"][-1]["role"] == "user"

    got = svc.get_chat_thread("p1", tid)                 # poll collects the reply
    assert got["busy"] is False
    assert got["messages"][-1]["role"] == "pm" and "what's next?" in got["messages"][-1]["text"]

    thread = svc.substrate.load_chat_thread("p1", tid)
    assert thread.session_id == "sess-1" and thread.turns_done == 1

    seen = {}
    def spy(**kw):
        seen.update(kw)
        return _fake_launch(session="sess-1")(**kw)
    svc.post_chat_message("p1", tid, "and then?", launch=spy)
    assert seen["resume"] is True and seen["prompt"] == "and then?"  # 2nd turn resumes


def test_chat_rename_scope_delete(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    tid = svc.create_chat("p1")["id"]
    assert svc.rename_chat("p1", tid, "renamed")["title"] == "renamed"
    assert svc.set_chat_scope("p1", tid, "full")["scope"] == "full"
    with pytest.raises(ValueError):
        svc.set_chat_scope("p1", tid, "bogus")
    svc.delete_chat("p1", tid)
    assert svc.list_chats("p1") == []
    with pytest.raises(NotFoundError):
        svc.get_chat_thread("p1", tid)


def test_chat_migrates_legacy_single_chat(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_chat("p1", [{"role": "user", "text": "old", "at": 1.0},
                                   {"role": "pm", "text": "reply", "at": 2.0}])
    chats = svc.list_chats("p1")                          # first access migrates
    assert len(chats) == 1 and chats[0]["messages"] == 2
    assert not (svc.substrate.program_dir("p1") / "chat.md").exists()


def test_pm_chat_appends_and_persists(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="cure",
                                       status=ProgramStatus.ACTIVE))
    tid = svc.create_chat("p1")["id"]
    svc.post_chat_message("p1", tid, "what's next?", launch=_fake_launch("answer to"))
    out = svc.get_chat_thread("p1", tid)
    assert out["messages"][-1]["text"].startswith("answer to")
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["user", "pm"]
    assert out["messages"][0]["text"] == "what's next?"


def test_pm_chat_rejects_empty(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g"))
    tid = svc.create_chat("p1")["id"]
    with pytest.raises(ValueError):
        svc.post_chat_message("p1", tid, "   ", launch=_fake_launch())


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
