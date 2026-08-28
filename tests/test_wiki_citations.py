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


def test_citations_endpoint(wiki_bundle):
    substrate, pid = wiki_bundle
    wiki_store.write_page(substrate, pid, _src("sources/result-wt-r1.md", "result:wt-r1"))
    wiki_store.write_page(substrate, pid, _concept("concepts/a.md", "/sources/result-wt-r1.md"))
    r = _client(substrate).get(f"/api/programs/{pid}/wiki/citations/result:wt-r1")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()] == ["a"]


def test_citations_endpoint_404s_for_an_unknown_program(substrate):
    assert _client(substrate).get("/api/programs/nope/wiki/citations/result:wt-r1").status_code == 404
