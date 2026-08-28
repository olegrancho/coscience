from urllib.parse import quote

from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_read, wiki_store
from coscience.http_api import build_app
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _src(path, origin):
    return wiki_okf.Page(path=path, type="Source", title="S",
                         body="# Summary\n\n" + "x" * 300, extra={"origin": origin})


def _concept(path, resource):
    return wiki_okf.Page(
        path=path, type="Concept", title=path.rsplit("/", 1)[-1][:-3],
        body="# Definition\n\n" + "x" * 300,
        sources=[wiki_okf.Source(id="wt-r1", resource=resource, title="S")])


def _concept_multi(path, resources):
    """Like `_concept`, but with one `sources[]` entry per resource — for
    exercising a page that cites several sources and only one is a match."""
    return wiki_okf.Page(
        path=path, type="Concept", title=path.rsplit("/", 1)[-1][:-3],
        body="# Definition\n\n" + "x" * 300,
        sources=[wiki_okf.Source(id=f"s{i}", resource=r, title="S")
                 for i, r in enumerate(resources)])


def test_finds_pages_citing_an_object():
    pages = [_src("sources/result-wt-r1.md", "result:wt-r1"),
             _concept("concepts/a.md", "/sources/result-wt-r1.md"),
             _concept("concepts/b.md", "/sources/result-other.md")]
    out = wiki_read.citing_pages(pages, "result:wt-r1")
    assert [c["path"] for c in out] == ["concepts/a.md"]
    assert out[0]["slug"] == "a"


def test_unknown_object_yields_nothing_rather_than_raising():
    assert wiki_read.citing_pages([], "result:nope") == []


def test_a_source_page_does_not_cite_itself():
    pages = [_src("sources/result-wt-r1.md", "result:wt-r1")]
    assert wiki_read.citing_pages(pages, "result:wt-r1") == []


def test_finds_pages_citing_an_artifact_version_with_a_hyphenated_id():
    # aid "fig-1" itself contains a hyphen — the reverse of oidForSourceSlug's
    # last-hyphen split on the frontend; this is the lookup-side counterpart.
    pages = [_src("sources/artifact-fig-1-v3.md", "artifact:fig-1@v3"),
             _concept("concepts/a.md", "/sources/artifact-fig-1-v3.md")]
    out = wiki_read.citing_pages(pages, "artifact:fig-1@v3")
    assert [c["path"] for c in out] == ["concepts/a.md"]


def test_a_page_citing_multiple_sources_matches_on_the_one_that_hits():
    pages = [_src("sources/result-wt-r1.md", "result:wt-r1"),
             _src("sources/result-other.md", "result:other"),
             _concept_multi("concepts/a.md",
                            ["/sources/result-other.md", "/sources/result-wt-r1.md"])]
    out = wiki_read.citing_pages(pages, "result:wt-r1")
    assert [c["path"] for c in out] == ["concepts/a.md"]


def test_a_source_page_cited_by_two_pages_returns_both():
    pages = [_src("sources/result-wt-r1.md", "result:wt-r1"),
             _concept("concepts/a.md", "/sources/result-wt-r1.md"),
             _concept("concepts/b.md", "/sources/result-wt-r1.md")]
    out = wiki_read.citing_pages(pages, "result:wt-r1")
    assert [c["path"] for c in out] == ["concepts/a.md", "concepts/b.md"]


def test_citations_endpoint(wiki_bundle):
    substrate, pid = wiki_bundle
    wiki_store.write_page(substrate, pid, _src("sources/result-wt-r1.md", "result:wt-r1"))
    wiki_store.write_page(substrate, pid, _concept("concepts/a.md", "/sources/result-wt-r1.md"))
    r = _client(substrate).get(f"/api/programs/{pid}/wiki/citations/result:wt-r1")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()] == ["a"]


def test_citations_endpoint_404s_for_an_unknown_program(substrate):
    assert _client(substrate).get("/api/programs/nope/wiki/citations/result:wt-r1").status_code == 404


def test_citations_endpoint_for_an_artifact_oid(wiki_bundle):
    """An artifact oid is `artifact:<aid>@<vid>` — the `@` must genuinely
    traverse routing, not just survive because it's harmless."""
    substrate, pid = wiki_bundle
    wiki_store.write_page(substrate, pid, _src("sources/artifact-fig-1-v3.md", "artifact:fig-1@v3"))
    wiki_store.write_page(substrate, pid, _concept("concepts/a.md", "/sources/artifact-fig-1-v3.md"))
    r = _client(substrate).get(f"/api/programs/{pid}/wiki/citations/artifact:fig-1@v3")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()] == ["a"]


def test_citations_endpoint_for_an_oid_containing_a_slash(wiki_bundle):
    """`{oid:path}` exists because an id may contain `/` (see http_api.py's
    comment on this route). Starlette's default `{oid}` string converter uses
    the regex `[^/]+` (starlette.convertors.StringConvertor) — it matches `@`
    and `:` just fine, so a test built only around `@` would pass under
    EITHER converter and prove nothing about which one is wired up. Only a
    literal `/` in the decoded oid actually discriminates. Build the request
    the way the frontend does — `encodeURIComponent(oid)` before the fetch —
    so this exercises real percent-decoding through ASGI, not a hand-crafted
    path string."""
    substrate, pid = wiki_bundle
    oid = "artifact:sub/fig@v1"
    wiki_store.write_page(substrate, pid, _src("sources/artifact-sub-fig-v1.md", oid))
    wiki_store.write_page(substrate, pid, _concept("concepts/a.md", "/sources/artifact-sub-fig-v1.md"))
    encoded = quote(oid, safe="")
    r = _client(substrate).get(f"/api/programs/{pid}/wiki/citations/{encoded}")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()] == ["a"]

