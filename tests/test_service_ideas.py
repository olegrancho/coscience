import pytest

from coscience.models import Program
from coscience.service import NotFoundError, Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    return svc


def test_add_list_idea(tmp_path):
    svc = _svc(tmp_path)
    idea = svc.add_idea("p1", "try a wheel sieve")
    assert idea["text"] == "try a wheel sieve"
    assert idea["source"] == "human"
    assert idea["protected"] is True                   # human ideas are protected
    pool = svc.list_ideas("p1")
    assert pool["summary"] == ""
    assert [i["id"] for i in pool["ideas"]] == [idea["id"]]


def test_human_can_delete_any_idea(tmp_path):
    svc = _svc(tmp_path)
    a = svc.add_idea("p1", "pm lead", source="pm")
    b = svc.add_idea("p1", "human lead", source="human")
    svc.delete_idea("p1", a["id"], by="human")
    svc.delete_idea("p1", b["id"], by="human")
    assert svc.list_ideas("p1")["ideas"] == []


def test_pm_cannot_delete_protected(tmp_path):
    svc = _svc(tmp_path)
    pm = svc.add_idea("p1", "pm lead", source="pm")
    human = svc.add_idea("p1", "human lead", source="human")
    svc.delete_idea("p1", pm["id"], by="pm")           # ok: own + unprotected
    with pytest.raises(ValueError):
        svc.delete_idea("p1", human["id"], by="pm")    # human idea is protected


def test_pin_protects_and_unpin_releases(tmp_path):
    svc = _svc(tmp_path)
    idea = svc.add_idea("p1", "pm lead", source="pm")
    svc.set_idea_pin("p1", idea["id"], True)
    with pytest.raises(ValueError):
        svc.delete_idea("p1", idea["id"], by="pm")
    svc.set_idea_pin("p1", idea["id"], False)
    svc.delete_idea("p1", idea["id"], by="pm")         # now deletable again
    assert svc.list_ideas("p1")["ideas"] == []


def test_comment_protects_and_shows(tmp_path):
    svc = _svc(tmp_path)
    idea = svc.add_idea("p1", "pm lead", source="pm")
    updated = svc.add_idea_comment("p1", idea["id"], "promising, keep it")
    assert updated["protected"] is True
    assert updated["comments"][0]["text"] == "promising, keep it"
    with pytest.raises(ValueError):
        svc.delete_idea("p1", idea["id"], by="pm")


def test_empty_idea_text_rejected(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.add_idea("p1", "   ")


def test_idea_ops_on_missing_program_or_idea(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(NotFoundError):
        svc.list_ideas("nope")
    with pytest.raises(NotFoundError):
        svc.delete_idea("p1", "ghost")
    with pytest.raises(NotFoundError):
        svc.add_idea_comment("p1", "ghost", "x")
