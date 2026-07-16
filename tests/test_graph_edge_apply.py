from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_pm_adds_valid_edge_and_drops_invalid(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1"))
    ops = [
        {"op": "add", "type": "builds_on", "src": "s2", "dst": "s1", "rationale": "uses it"},
        {"op": "add", "type": "builds_on", "src": "s2", "dst": "ghost", "rationale": "bad"},  # missing endpoint
        {"op": "add", "type": "builds_on", "src": "s2", "dst": "s1"},                          # no rationale
    ]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_added"] == 1
    s2 = svc.substrate.load_sprint("s2")
    assert [(e["type"], e["dst"], e["source"]) for e in s2.edges] == [("builds_on", "s1", "pm")]


def test_pm_cannot_delete_human_edge(tmp_path):
    svc = _svc(tmp_path)
    human_edge = graph.new_edge("builds_on", "s2", "s1", "human", by="u", rationale="mine")
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1",
                                     edges=[human_edge]))
    ops = [{"op": "delete", "type": "builds_on", "src": "s2", "dst": "s1"}]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_removed"] == 0
    assert len(svc.substrate.load_sprint("s2").edges) == 1     # human edge survives


def test_delete_then_add_reverse_in_one_batch(tmp_path):
    # PM corrects an edge's direction in a single cycle: delete s2->s1, add s1->s2.
    # The add must NOT be rejected as a cycle against the just-deleted edge.
    svc = _svc(tmp_path)
    old = graph.new_edge("builds_on", "s2", "s1", "pm", rationale="wrong way")
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1",
                                     edges=[old]))
    ops = [
        {"op": "delete", "type": "builds_on", "src": "s2", "dst": "s1"},
        {"op": "add", "type": "builds_on", "src": "s1", "dst": "s2", "rationale": "corrected"},
    ]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert (out["edges_removed"], out["edges_added"]) == (1, 1)
    assert svc.substrate.load_sprint("s2").edges == []
    assert [(e["src"], e["dst"]) for e in svc.substrate.load_sprint("s1").edges] == [("s1", "s2")]


def test_add_with_null_rationale_is_dropped(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1"))
    ops = [{"op": "add", "type": "builds_on", "src": "s2", "dst": "s1", "rationale": None}]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_added"] == 0                          # explicit null == no rationale
    assert svc.substrate.load_sprint("s2").edges == []


def test_pruning_idea_cascades_inbound_edges(tmp_path):
    # Pruning idea A must drop every edge pointing AT A (on ideas AND sprints), or
    # a surviving node keeps a dangling reference.
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1",
                                     edges=[graph.new_edge("inspired_by", "s1", "A", "pm", rationale="r")]))
    ideas = [Idea(id="A", text="doomed", source="pm"),
             Idea(id="B", text="refs A", source="pm",
                  edges=[graph.new_edge("inspired_by", "B", "A", "pm", rationale="r")])]
    svc.substrate.save_ideas("p1", "seed", ideas)
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(delete_idea_ids=["A"])]), force=True)
    assert out["ideas_removed"] == 1
    _s, after = svc.substrate.load_ideas("p1")
    assert {i.id for i in after} == {"B"}
    assert next(i for i in after if i.id == "B").edges == []   # idea-side inbound cascaded
    assert svc.substrate.load_sprint("s1").edges == []          # sprint-side inbound cascaded


def test_pm_deletes_its_own_edge(tmp_path):
    svc = _svc(tmp_path)
    pm_edge = graph.new_edge("builds_on", "s2", "s1", "pm", rationale="mine")
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1",
                                     edges=[pm_edge]))
    ops = [{"op": "delete", "type": "builds_on", "src": "s2", "dst": "s1"}]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_removed"] == 1
    assert svc.substrate.load_sprint("s2").edges == []
