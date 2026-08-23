from coscience import wiki_okf, wiki_read


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    return wiki_okf.Page(path=path, **kw)


def test_a_title_hit_outranks_a_body_hit():
    title = _page("concepts/lease.md", title="Compute lease")
    body = _page("concepts/other.md", title="Other", body="mentions a compute lease here")
    hits = wiki_read.search([body, title], "compute lease")
    assert [h["path"] for h in hits] == ["concepts/lease.md", "concepts/other.md"]


def test_an_alias_hit_is_found_and_ranks_between_title_and_body():
    aliased = _page("concepts/a.md", title="Compute lease", aliases=["leases.json"])
    hits = wiki_read.search([aliased], "leases.json")
    assert [h["path"] for h in hits] == ["concepts/a.md"]


def test_search_is_case_insensitive_and_returns_an_excerpt_around_the_hit():
    # Filler on both sides has to exceed the excerpt window, or the window covers
    # the whole body and there is nothing to truncate.
    p = _page("concepts/a.md", title="A",
              body="x" * 200 + " HYDROLYSIS matters " + "y" * 200)
    hit = wiki_read.search([p], "hydrolysis")[0]
    assert "HYDROLYSIS" in hit["excerpt"]
    assert len(hit["excerpt"]) < len(p.body)
    assert hit["excerpt"].startswith("…") and hit["excerpt"].endswith("…")


def test_a_short_body_is_excerpted_whole_without_ellipses():
    p = _page("concepts/a.md", title="A", body="a lease is held while awake")
    hit = wiki_read.search([p], "lease")[0]
    assert hit["excerpt"] == "a lease is held while awake"


def test_a_query_matching_nothing_returns_nothing():
    assert wiki_read.search([_page("concepts/a.md")], "zzzz") == []


def test_a_blank_query_returns_nothing_rather_than_everything():
    # A blank box must not dump the whole bundle through the wire.
    assert wiki_read.search([_page("concepts/a.md")], "   ") == []


def test_search_respects_the_limit():
    pages = [_page(f"concepts/p{i}.md", title="lease") for i in range(10)]
    assert len(wiki_read.search(pages, "lease", limit=3)) == 3
