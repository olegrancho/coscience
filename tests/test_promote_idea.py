"""K5: a human promotes a pool idea into a sprint, ending the way the PM's promotion does."""

import pytest

from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_a_promotion_makes_the_sprint_and_moves_the_idea_lineage_onto_it(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="SB", status=SprintStatus.DONE, goals="base", program="p1"))
    svc.substrate.save_ideas("p1", "", [
        Idea(id="i1", text="try the thing",
             edges=[graph.new_edge("inspired_by", "i1", "SB", "pm", rationale="r")]),
        Idea(id="i2", text="another direction"),
    ])

    svc.submit_sprint(id="p1-h1", goals="try the thing", plan=["x"], program="p1", from_idea="i1")

    _summary, ideas = svc.substrate.load_ideas("p1")
    assert [i.id for i in ideas] == ["i2"]          # the idea became the sprint
    edges = svc.substrate.load_sprint("p1-h1").edges
    assert [(e["type"], e["src"], e["dst"]) for e in edges] == [("inspired_by", "p1-h1", "SB")]


def test_promoting_an_idea_that_is_not_in_the_pool_creates_nothing(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.submit_sprint(id="p1-h1", goals="g", plan=["x"], program="p1", from_idea="nope")
    assert not (svc.substrate.sprint_dir("p1-h1") / "sprint.md").is_file()
