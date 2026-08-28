from coscience import wiki_graph, wiki_okf


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 300 + "\n")
    return wiki_okf.Page(path=path, **kw)


def _ids(items):
    return sorted(i["id"] for i in items)


def test_only_concept_entity_synthesis_become_nodes():
    pages = [
        _page("concepts/a.md"),
        _page("entities/b.md", type="Entity"),
        _page("syntheses/c.md", type="Synthesis"),
        _page("sources/d.md", type="Source"),
        _page("questions/e.md", type="Question"),
    ]
    g = wiki_graph.build(pages)
    assert _ids(g["nodes"]) == ["concepts/a.md", "entities/b.md", "syntheses/c.md"]


def test_graph_excluded_page_is_not_a_node():
    pages = [_page("concepts/a.md"), _page("concepts/b.md", graph_excluded=True)]
    g = wiki_graph.build(pages)
    assert _ids(g["nodes"]) == ["concepts/a.md"]


def test_typed_edges_come_from_relations_and_carry_their_metadata():
    rel = wiki_okf.Relation(type="refines", target="/concepts/b.md",
                            confidence="high", source="wt-r1")
    pages = [_page("concepts/a.md", relations=[rel],
                   body="# D\n\nSee [b](/concepts/b.md).\n" + "x" * 300),
             _page("concepts/b.md")]
    g = wiki_graph.build(pages)
    typed = [e for e in g["edges"] if e["typed"]]
    assert len(typed) == 1
    e = typed[0]
    assert (e["src"], e["dst"], e["type"]) == ("concepts/a.md", "concepts/b.md", "refines")
    assert (e["confidence"], e["source"]) == ("high", "wt-r1")


def test_untyped_body_link_becomes_an_edge_unless_a_typed_relation_covers_it():
    # a -> b is covered by a relation; a -> c is a bare body link.
    rel = wiki_okf.Relation(type="refines", target="/concepts/b.md",
                            confidence="high", source="wt-r1")
    body = "# D\n\n[b](/concepts/b.md) and [c](/concepts/c.md)\n" + "x" * 300
    pages = [_page("concepts/a.md", relations=[rel], body=body),
             _page("concepts/b.md"), _page("concepts/c.md")]
    g = wiki_graph.build(pages)
    untyped = [(e["src"], e["dst"]) for e in g["edges"] if not e["typed"]]
    assert untyped == [("concepts/a.md", "concepts/c.md")]


def test_body_link_to_a_non_node_page_is_not_an_edge():
    body = "# D\n\n[src](/sources/s.md)\n" + "x" * 300
    pages = [_page("concepts/a.md", body=body), _page("sources/s.md", type="Source")]
    g = wiki_graph.build(pages)
    assert g["edges"] == []
