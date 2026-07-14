import pytest

from coscience.models import Program
from coscience.service import NotFoundError, Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    return svc


def test_add_then_list(tmp_path):
    svc = _svc(tmp_path)
    thread = svc.add_guidance("p1", "focus on assays")
    assert thread["messages"][0]["text"] == "focus on assays"
    assert thread["target"] == "pm"
    assert thread["id"]
    assert svc.list_guidance("p1") == [thread]


def test_reply_appends_to_same_thread_and_reopens(tmp_path):
    svc = _svc(tmp_path)
    thread = svc.add_guidance("p1", "alpha")
    svc.complete_guidance_thread("p1", thread["id"])
    assert svc.list_guidance("p1")[0]["status"] == "complete"
    updated = svc.add_guidance("p1", "more detail", thread_id=thread["id"])
    assert len(updated["messages"]) == 2
    assert updated["messages"][-1]["text"] == "more detail"
    assert updated["status"] == "open"          # reopened by the new human message


def test_seen_clears_agent_unseen(tmp_path):
    from coscience import threads as _threads
    svc = _svc(tmp_path)
    thread = svc.add_guidance("p1", "alpha")
    guidance_threads = svc.substrate.load_guidance("p1")
    _threads.append(guidance_threads[0], "pm", "noted", "", now=2.0)
    svc.substrate.save_guidance("p1", guidance_threads)
    assert svc.list_guidance("p1")[0]["agent_unseen"] is True
    svc.seen_guidance_thread("p1", thread["id"])
    assert svc.list_guidance("p1")[0]["agent_unseen"] is False


def test_remove_one_thread(tmp_path):
    svc = _svc(tmp_path)
    a = svc.add_guidance("p1", "alpha")
    b = svc.add_guidance("p1", "beta")
    svc.remove_guidance("p1", a["id"])
    assert svc.list_guidance("p1") == [b]


def test_remove_unknown_id_is_noop(tmp_path):
    svc = _svc(tmp_path)
    a = svc.add_guidance("p1", "alpha")
    svc.remove_guidance("p1", "does-not-exist")
    assert svc.list_guidance("p1") == [a]


def test_empty_guidance_text_rejected(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.add_guidance("p1", "   ")


def test_guidance_missing_program_raises(tmp_path):
    svc = Service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.add_guidance("nope", "x")
    with pytest.raises(NotFoundError):
        svc.list_guidance("nope")
    with pytest.raises(NotFoundError):
        svc.remove_guidance("nope", "x")
    with pytest.raises(NotFoundError):
        svc.complete_guidance_thread("nope", "x")
    with pytest.raises(NotFoundError):
        svc.seen_guidance_thread("nope", "x")


def test_guidance_thread_not_found(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(NotFoundError):
        svc.complete_guidance_thread("p1", "ghost")
    with pytest.raises(NotFoundError):
        svc.seen_guidance_thread("p1", "ghost")
    with pytest.raises(NotFoundError):
        svc.add_guidance("p1", "x", thread_id="ghost")
