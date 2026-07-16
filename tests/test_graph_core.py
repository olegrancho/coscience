from coscience import graph
from coscience.models import Idea, Sprint, SprintStatus


def test_core_types_enabled_extended_not():
    assert graph.CORE_TYPES == {"inspired_by", "builds_on", "supersedes", "confirms", "refutes"}
    assert "contradicts" in graph.EXTENDED_TYPES
    assert graph.ENABLED_TYPES == graph.CORE_TYPES          # extended defined but off
    assert graph.EXTENDED_TYPES & graph.ENABLED_TYPES == set()


def test_node_stage_and_kind():
    idea = Idea(id="i1", text="x")
    running = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g")
    done = Sprint(id="s2", status=SprintStatus.DONE, goals="g")
    assert graph.node_kind(idea) == graph.IDEA
    assert graph.node_kind(done) == graph.EXPERIMENT
    assert graph.node_stage(idea) == graph.IDEA
    assert graph.node_stage(running) == graph.EXPERIMENT
    assert graph.node_stage(done) == graph.RESULT          # done experiment == result


def test_new_edge_is_deterministic_and_shaped():
    e1 = graph.new_edge("builds_on", "s2", "s1", "pm", by="pm", at=5.0, rationale="uses method")
    e2 = graph.new_edge("builds_on", "s2", "s1", "human")
    assert e1["id"] == e2["id"]                            # id depends only on (type, src, dst)
    assert e1["id"] == graph.edge_id("builds_on", "s2", "s1")
    assert set(e1) == {"id", "type", "src", "dst", "source", "by", "at",
                       "rationale", "confidence", "evidence"}
    assert (e1["type"], e1["src"], e1["dst"], e1["source"]) == ("builds_on", "s2", "s1", "pm")
