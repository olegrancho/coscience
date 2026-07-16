from coscience import graph
from coscience.models import Idea, Sprint, SprintStatus


def _nodes():
    return [
        Idea(id="i1", text="a"),
        Idea(id="i2", text="b"),
        Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g"),
        Sprint(id="s2", status=SprintStatus.DONE, goals="g"),
        Sprint(id="s3", status=SprintStatus.DONE, goals="g"),
    ]


def test_valid_core_edges():
    ns = _nodes()
    assert graph.validate_edge(graph.new_edge("inspired_by", "i2", "i1", "pm", rationale="r"), ns, []) is None
    assert graph.validate_edge(graph.new_edge("builds_on", "s2", "s1", "pm", rationale="r"), ns, []) is None
    ev = graph.new_edge("confirms", "s3", "s2", "pm", rationale="r", confidence="high")
    assert graph.validate_edge(ev, ns, []) is None


def test_rejects_disabled_type():
    ns = _nodes()
    e = graph.new_edge("refines", "i2", "i1", "pm", rationale="r")   # extended, off
    assert graph.validate_edge(e, ns, []) == "type not enabled: refines"


def test_rejects_missing_endpoint_and_self_edge():
    ns = _nodes()
    assert "endpoint" in graph.validate_edge(graph.new_edge("builds_on", "s2", "nope", "pm", rationale="r"), ns, [])
    assert graph.validate_edge(graph.new_edge("builds_on", "s2", "s2", "pm", rationale="r"), ns, []) == "self-edge"


def test_rejects_illegal_kind_pair():
    ns = _nodes()
    # builds_on is experiment->experiment; an idea source is illegal
    e = graph.new_edge("builds_on", "i1", "s1", "pm", rationale="r")
    assert graph.validate_edge(e, ns, []) == "illegal kind pair"


def test_evidential_requires_done_endpoints_and_confidence():
    ns = _nodes()
    # s1 is EXECUTING (not a result) -> refutes not allowed
    e = graph.new_edge("refutes", "s2", "s1", "pm", rationale="r", confidence="high")
    assert "done" in graph.validate_edge(e, ns, [])
    # both done but no confidence
    e2 = graph.new_edge("confirms", "s3", "s2", "pm", rationale="r")
    assert "confidence" in graph.validate_edge(e2, ns, [])


def test_rejects_lineage_cycle():
    ns = _nodes()
    existing = [graph.new_edge("builds_on", "s1", "s2", "pm")]   # s1 -> s2
    # adding s2 -> s1 would close a cycle
    e = graph.new_edge("builds_on", "s2", "s1", "pm", rationale="r")
    assert graph.validate_edge(e, ns, existing) == "would create cycle"
