from dataclasses import dataclass, field
from coscience import graph


@dataclass
class _N:                       # minimal node stand-in: id + outbound edge list
    id: str
    edges: list = field(default_factory=list)


def test_all_edges_and_reverse_index():
    a = _N("a", [graph.new_edge("inspired_by", "a", "b", "pm")])
    b = _N("b", [graph.new_edge("builds_on", "b", "c", "pm")])
    c = _N("c")
    idx = graph.build_reverse_index(graph.all_edges([a, b, c]))
    assert [e["src"] for e in idx["b"]] == ["a"]      # a -> b is incoming to b
    assert [e["src"] for e in idx["c"]] == ["b"]
    assert "a" not in idx                              # nothing points at a


def test_repoint_moves_outbound_and_rewrites_inbound():
    # B inspired_by A (inbound to A); A inspired_by X (outbound from A).
    a = _N("a", [graph.new_edge("inspired_by", "a", "x", "pm")])
    b = _N("b", [graph.new_edge("inspired_by", "b", "a", "pm")])
    s = _N("SA")                                       # the new sprint node
    x = _N("x")
    changed = graph.repoint_edges("a", "SA", [a, b, s, x])
    assert a.edges == []                               # drained
    assert [(e["src"], e["dst"]) for e in s.edges] == [("SA", "x")]   # outbound moved
    assert [(e["src"], e["dst"]) for e in b.edges] == [("b", "SA")]   # inbound rewritten
    assert changed == {"SA", "b"}
    assert s.edges[0]["id"] == graph.edge_id("inspired_by", "SA", "x")  # id refreshed


def test_repoint_drops_would_be_self_loop():
    # A node pre-seeded with an edge pointing AT the node being repointed: after
    # repoint that edge would become new->new; it must be dropped, not kept.
    old = _N("old", [])
    new = _N("new", [graph.new_edge("inspired_by", "new", "old", "system")])
    changed = graph.repoint_edges("old", "new", [old, new])
    assert new.edges == []                             # self-loop dropped
    assert "new" in changed


def test_repoint_is_idempotent_on_outbound():
    # Simulate a resumed promotion: the sprint already holds the transferred edge,
    # and the not-yet-removed old idea still holds its original outbound edge. A
    # second repoint must NOT append a duplicate.
    old = _N("A", [graph.new_edge("inspired_by", "A", "X", "pm")])
    new = _N("SA", [graph.new_edge("inspired_by", "SA", "X", "pm")])   # already transferred
    x = _N("X")
    graph.repoint_edges("A", "SA", [old, new, x])
    assert len(new.edges) == 1                         # not duplicated
    assert (new.edges[0]["src"], new.edges[0]["dst"]) == ("SA", "X")


def test_drop_evidential_incident():
    done = _N("s1", [graph.new_edge("refutes", "s1", "s2", "pm", confidence="high")])
    lineage = _N("s3", [graph.new_edge("builds_on", "s3", "s2", "pm")])
    changed = graph.drop_evidential_incident("s2", [done, lineage])
    assert done.edges == []                            # evidential refutes -> s2 dropped
    assert len(lineage.edges) == 1                     # lineage builds_on -> s2 kept
    assert changed == {"s1"}
