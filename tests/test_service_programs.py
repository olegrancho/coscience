import json

import pytest

from coscience.models import Program, ProgramStatus, Sprint, SprintStatus, Step
from coscience.service import NotFoundError, Service


def test_list_and_get_program(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="Cancer", goals="cure"))
    svc.substrate.save_sprint(Sprint(id="p1-s1", status=SprintStatus.PROPOSED,
                                     goals="assay", plan=[Step("s", "true")], program="p1"))
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
