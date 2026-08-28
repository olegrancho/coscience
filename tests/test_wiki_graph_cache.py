import json

from coscience import wiki_graph, wiki_okf, wiki_store


def _write(substrate, pid, path, body_pad="x" * 300, title="A"):
    page = wiki_okf.Page(path=path, type="Concept", title=title,
                         body="# Definition\n\n" + body_pad + "\n")
    wiki_store.write_page(substrate, pid, page)


def test_second_call_reads_the_cache(wiki_bundle):
    substrate, pid = wiki_bundle
    _write(substrate, pid, "concepts/a.md")
    first = wiki_graph.cached_build(substrate, pid)
    cache = wiki_store.state_dir(substrate, pid) / "graph.json"
    assert cache.exists()
    # Corrupt the cached payload; a cache hit must return the corrupted value,
    # proving the second call did not rebuild.
    blob = json.loads(cache.read_text())
    blob["graph"]["nodes"][0]["title"] = "FROM-CACHE"
    cache.write_text(json.dumps(blob))
    second = wiki_graph.cached_build(substrate, pid)
    assert second["nodes"][0]["title"] == "FROM-CACHE"
    assert first["nodes"][0]["title"] == "A"


def test_same_size_content_change_still_rebuilds(wiki_bundle):
    """The defect this catches: keying the cache on (mtime, size), as the parent
    spec 10 says. Both bodies below are the same byte length, so a size-based
    key serves a stale graph."""
    substrate, pid = wiki_bundle
    _write(substrate, pid, "concepts/a.md", title="AAAA")
    wiki_graph.cached_build(substrate, pid)
    _write(substrate, pid, "concepts/a.md", title="BBBB")   # identical length
    again = wiki_graph.cached_build(substrate, pid)
    assert again["nodes"][0]["title"] == "BBBB"


def test_a_corrupt_cache_file_rebuilds_instead_of_raising(wiki_bundle):
    substrate, pid = wiki_bundle
    _write(substrate, pid, "concepts/a.md")
    cache = wiki_store.state_dir(substrate, pid) / "graph.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not json")
    g = wiki_graph.cached_build(substrate, pid)
    assert [n["id"] for n in g["nodes"]] == ["concepts/a.md"]
