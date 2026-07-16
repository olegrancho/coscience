from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_promotion_transfers_idea_edges_onto_sprint(tmp_path):
    svc = _svc(tmp_path)
    # idea A (to be promoted) has an outbound edge to idea X; idea B points AT A.
    ideas = [
        Idea(id="A", text="promote me", source="pm",
             edges=[graph.new_edge("inspired_by", "A", "X", "pm", rationale="r")]),
        Idea(id="B", text="depends on A", source="pm",
             edges=[graph.new_edge("inspired_by", "B", "A", "pm", rationale="r")]),
        Idea(id="X", text="root", source="pm"),
    ]
    svc.substrate.save_ideas("p1", "seed", ideas)

    out = PMCycleOutput(proposals=[ProposedSprint(suffix="go", goals="do it",
                                                  plan=["x"], from_idea="A")])
    pm_beat(svc.substrate, "p1", FakeReasoner([out]), force=True)

    sid = "p1-c0-go"
    sprint = svc.substrate.load_sprint(sid)
    # A's outbound edge now belongs to the sprint, repointed as its source.
    assert [(e["src"], e["dst"]) for e in sprint.edges] == [(sid, "X")]

    _summary, ideas_after = svc.substrate.load_ideas("p1")
    by_id = {i.id: i for i in ideas_after}
    assert "A" not in by_id                                   # idea consumed
    # B's inbound edge to A now points at the sprint.
    assert [(e["src"], e["dst"]) for e in by_id["B"].edges] == [("B", sid)]
