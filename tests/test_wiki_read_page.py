from coscience import wiki_okf, wiki_read


def _page(path, body="", relations=(), **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    return wiki_okf.Page(path=path, body=body,
                         relations=[wiki_okf.Relation(**r) for r in relations], **kw)


def test_relations_report_whether_the_target_exists():
    # A broken relation must render as broken, not vanish: OKF tolerates dangling
    # links and the reader needs to see one.
    a = _page("concepts/a.md", relations=[
        {"type": "requires", "target": "/concepts/b.md", "confidence": "high", "source": "c1"},
        {"type": "requires", "target": "/concepts/gone.md", "source": "c1"}])
    b = _page("concepts/b.md", title="B")
    out = wiki_read.page_detail(a, [a, b])
    by_target = {r["target"]: r for r in out["relations"]}
    assert by_target["concepts/b.md"]["exists"] is True
    assert by_target["concepts/b.md"]["title"] == "B"
    assert by_target["concepts/gone.md"]["exists"] is False


def test_relations_are_ordered_by_the_frozen_vocabulary():
    a = _page("concepts/a.md", relations=[
        {"type": "extends", "target": "/concepts/b.md", "source": "c1"},
        {"type": "is_a", "target": "/concepts/c.md", "source": "c1"}])
    out = wiki_read.page_detail(a, [a])
    assert [r["type"] for r in out["relations"]] == ["is_a", "extends"]


def test_backlinks_find_both_body_links_and_typed_relations():
    target = _page("concepts/target.md")
    plain = _page("concepts/plain.md", body="see [t](/concepts/target.md)")
    typed = _page("concepts/typed.md", body="see [t](/concepts/target.md)",
                  relations=[{"type": "refines", "target": "/concepts/target.md",
                              "source": "c1"}])
    out = wiki_read.page_detail(target, [target, plain, typed])
    by_path = {b["path"]: b for b in out["backlinks"]}
    assert by_path["concepts/plain.md"]["typed"] == []
    assert by_path["concepts/typed.md"]["typed"] == ["refines"]


def test_a_page_does_not_backlink_to_itself():
    a = _page("concepts/a.md", body="see [me](/concepts/a.md)")
    assert wiki_read.page_detail(a, [a])["backlinks"] == []


def test_backlinks_see_a_wikilink_too():
    # Phase 1's live run wrote [[concepts/x]]; until lint rewrites them the reader
    # must still see the inbound link.
    target = _page("concepts/target.md")
    src = _page("concepts/src.md", body="see [[concepts/target]]")
    out = wiki_read.page_detail(target, [target, src])
    assert [b["path"] for b in out["backlinks"]] == ["concepts/src.md"]


def test_sources_become_provenance_refs_by_kind():
    a = _page("concepts/a.md")
    a.sources = [wiki_okf.Source(id="c1", resource="/results/r7.md", title="R7"),
                 wiki_okf.Source(id="c2", resource="/programs/p1/artifacts/fig/v1",
                                 title="fig")]
    out = wiki_read.page_detail(a, [a])
    refs = {s["id"]: s for s in out["sources"]}
    assert refs["c1"]["kind"] == "result" and refs["c1"]["href"] == "/results/r7"
    assert refs["c2"]["kind"] == "artifact"
    assert refs["c2"]["href"] == "/programs/p1/artifacts/fig"


def test_an_unrecognised_resource_is_marked_unknown_not_dropped():
    a = _page("concepts/a.md")
    a.sources = [wiki_okf.Source(id="c1", resource="https://example.org/x", title="x")]
    out = wiki_read.page_detail(a, [a])
    assert out["sources"][0]["kind"] == "unknown"
    assert out["sources"][0]["href"] == ""
    assert out["sources"][0]["resource"] == "https://example.org/x"


def test_page_detail_carries_trust_and_the_protected_section_verbatim():
    a = _page("concepts/a.md", body="# Definition\n\nd\n\n# Human notes\n\nkeep me\n",
              verified=[{"by": "human:oleg", "at": 1.0}])
    out = wiki_read.page_detail(a, [a])
    assert out["trust"] == "human-reviewed"
    assert out["human_notes"] == "keep me"
    assert "# Human notes" in out["body"]
