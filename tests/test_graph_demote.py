from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_demote_transfers_lineage_and_drops_evidential(tmp_path):
    svc = _svc(tmp_path)
    # SA (to demote) has an inspired_by edge to SB (any->any, survives as idea->exp).
    # SC (done) refutes SA (inbound evidential, must be dropped).
    sa = Sprint(id="SA", status=SprintStatus.PROPOSED, goals="doomed", program="p1",
                edges=[graph.new_edge("inspired_by", "SA", "SB", "pm", rationale="r")])
    sb = Sprint(id="SB", status=SprintStatus.DONE, goals="base", program="p1")
    sc = Sprint(id="SC", status=SprintStatus.DONE, goals="counter", program="p1",
                edges=[graph.new_edge("refutes", "SC", "SA", "human", by="u",
                                      rationale="r", confidence="high")])
    for s in (sa, sb, sc):
        svc.substrate.save_sprint(s)

    result = svc.demote_sprint("SA", by="u")
    new_idea_id = result["idea"]["id"]

    _summary, ideas = svc.substrate.load_ideas("p1")
    idea = next(i for i in ideas if i.id == new_idea_id)
    # SA's outbound inspired_by edge (kind-valid across stages) moved onto the idea.
    assert [(e["type"], e["src"], e["dst"]) for e in idea.edges] == [("inspired_by", new_idea_id, "SB")]
    # The inbound evidential refutes edge on SC was dropped (idea has no result).
    assert svc.substrate.load_sprint("SC").edges == []
    # The demoted sprint keeps NO stale edges (drained on the object we saved).
    assert svc.substrate.load_sprint("SA").edges == []


def test_demote_drops_kind_illegal_lineage_edge(tmp_path):
    svc = _svc(tmp_path)
    # SB builds_on SA (experiment->experiment). Demoting SA repoints that edge onto
    # the new idea, making it builds_on->idea (illegal kind pair); it must be dropped.
    sa = Sprint(id="SA", status=SprintStatus.PROPOSED, goals="target", program="p1")
    sb = Sprint(id="SB", status=SprintStatus.EXECUTING, goals="dependent", program="p1",
                edges=[graph.new_edge("builds_on", "SB", "SA", "pm", rationale="r")])
    for s in (sa, sb):
        svc.substrate.save_sprint(s)
    svc.demote_sprint("SA", by="u")
    assert svc.substrate.load_sprint("SB").edges == []   # degraded builds_on->idea dropped


def test_delete_idea_cascades_inbound_edges(tmp_path):
    svc = _svc(tmp_path)
    ideas = [Idea(id="A", text="doomed", source="pm"),
             Idea(id="B", text="refs A", source="pm",
                  edges=[graph.new_edge("inspired_by", "B", "A", "pm", rationale="r")])]
    svc.substrate.save_ideas("p1", "seed", ideas)
    svc.delete_idea("p1", "A", by="human")
    _s, after = svc.substrate.load_ideas("p1")
    assert {i.id for i in after} == {"B"}
    assert after[0].edges == []                           # dangling inbound cascaded away
