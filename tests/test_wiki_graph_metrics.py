# tests/test_wiki_graph_metrics.py
from coscience import wiki_graph, wiki_okf


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 300 + "\n")
    return wiki_okf.Page(path=path, **kw)


def _rel(type_, target, source="wt-r1"):
    return wiki_okf.Relation(type=type_, target=target, confidence="high", source=source)


def _by_id(g):
    return {n["id"]: n for n in g["nodes"]}


def test_degree_counts_typed_and_untyped_edges():
    pages = [_page("concepts/a.md", relations=[_rel("refines", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    n = _by_id(wiki_graph.build(pages))
    assert (n["concepts/a.md"]["out_degree"], n["concepts/a.md"]["in_degree"]) == (1, 0)
    assert (n["concepts/b.md"]["out_degree"], n["concepts/b.md"]["in_degree"]) == (0, 1)


def test_contradicts_is_materialized_in_reverse():
    pages = [_page("concepts/a.md", relations=[_rel("contradicts", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    g = wiki_graph.build(pages)
    rev = [e for e in g["edges"] if e["materialized"]]
    assert len(rev) == 1
    assert (rev[0]["src"], rev[0]["dst"], rev[0]["type"]) == \
           ("concepts/b.md", "concepts/a.md", "contradicts")


def test_materialized_edges_do_not_count_toward_degree():
    """The defect this catches: counting the materialized reverse edge, which
    inflates BOTH ends of a single disagreement. Node size is degree, so that
    would draw a lie."""
    pages = [_page("concepts/a.md", relations=[_rel("contradicts", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    n = _by_id(wiki_graph.build(pages))
    assert (n["concepts/a.md"]["out_degree"], n["concepts/a.md"]["in_degree"]) == (1, 0)
    assert (n["concepts/b.md"]["out_degree"], n["concepts/b.md"]["in_degree"]) == (0, 1)


def test_orphan_is_degree_zero_only():
    pages = [_page("concepts/a.md", relations=[_rel("refines", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md"), _page("concepts/lonely.md")]
    n = _by_id(wiki_graph.build(pages))
    assert n["concepts/lonely.md"]["orphan"] is True
    assert n["concepts/a.md"]["orphan"] is False
    assert n["concepts/b.md"]["orphan"] is False


def test_cluster_ids_group_connected_components_ignoring_direction():
    pages = [_page("concepts/a.md", relations=[_rel("refines", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md"),
             _page("concepts/x.md", relations=[_rel("refines", "/concepts/y.md")],
                   body="# D\n\n[y](/concepts/y.md)\n" + "x" * 300),
             _page("concepts/y.md")]
    n = _by_id(wiki_graph.build(pages))
    assert n["concepts/a.md"]["cluster"] == n["concepts/b.md"]["cluster"]
    assert n["concepts/x.md"]["cluster"] == n["concepts/y.md"]["cluster"]
    assert n["concepts/a.md"]["cluster"] != n["concepts/x.md"]["cluster"]


def test_a_materialized_edge_alone_does_not_join_a_cluster_twice():
    """Cluster ids must be stable whether or not reverse edges exist."""
    pages = [_page("concepts/a.md", relations=[_rel("contradicts", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    n = _by_id(wiki_graph.build(pages))
    assert n["concepts/a.md"]["cluster"] == n["concepts/b.md"]["cluster"]
    assert sorted({v["cluster"] for v in n.values()}) == [0]
