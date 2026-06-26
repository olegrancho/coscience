import pytest

from coscience.models import Program
from coscience.service import NotFoundError, Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="t", goals="g"))
    return svc


def test_add_then_list(tmp_path):
    svc = _svc(tmp_path)
    note = svc.add_guidance("p1", "focus on assays")
    assert note["text"] == "focus on assays"
    assert note["id"]
    assert isinstance(note["added_at"], float)
    assert svc.list_guidance("p1") == [note]


def test_remove_one_note(tmp_path):
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


def test_guidance_missing_program_raises(tmp_path):
    svc = Service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.add_guidance("nope", "x")
    with pytest.raises(NotFoundError):
        svc.list_guidance("nope")
    with pytest.raises(NotFoundError):
        svc.remove_guidance("nope", "x")
