from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_store
from coscience.http_api import build_app
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _write(substrate, pid, path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 300 + "\n")
    wiki_store.write_page(substrate, pid, wiki_okf.Page(path=path, **kw))


def test_graph_endpoint_returns_nodes_and_edges(wiki_bundle):
    substrate, pid = wiki_bundle
    rel = wiki_okf.Relation(type="refines", target="/concepts/b.md",
                            confidence="high", source="wt-r1")
    _write(substrate, pid, "concepts/a.md", relations=[rel],
           body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300)
    _write(substrate, pid, "concepts/b.md")
    r = _client(substrate).get(f"/api/programs/{pid}/wiki/graph")
    assert r.status_code == 200
    body = r.json()
    assert sorted(n["id"] for n in body["nodes"]) == ["concepts/a.md", "concepts/b.md"]
    assert [e["type"] for e in body["edges"]] == ["refines"]


def test_graph_endpoint_404s_for_an_unknown_program(substrate):
    assert _client(substrate).get("/api/programs/nope/wiki/graph").status_code == 404
