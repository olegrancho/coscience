"""L6: click-through access serves the index, then only pages linked from opened ones."""
import io
import json

from coscience import wiki_nav


def _bundle(tmp_path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "index.md").write_text("# Index\n\n- [A](/concepts/a.md)\n- [Gone](/concepts/gone.md)\n")
    (tmp_path / "concepts" / "a.md").write_text("See [B](b.md) and [[c]].\n")
    (tmp_path / "concepts" / "b.md").write_text("B page\n")
    (tmp_path / "concepts" / "c.md").write_text("C page\n")
    (tmp_path / "concepts" / "hidden.md").write_text("nobody links here\n")
    return tmp_path


def test_only_the_index_is_open_at_first(tmp_path):
    nav = wiki_nav.Navigator(_bundle(tmp_path))
    assert nav.open("/concepts/a.md")[0] is False
    ok, path, text = nav.open("index.md")
    assert ok and path == "index.md" and "Index" in text


def test_links_on_opened_pages_become_reachable_relative_and_wikilinks_too(tmp_path):
    nav = wiki_nav.Navigator(_bundle(tmp_path))
    nav.open("index.md")
    assert nav.open("/concepts/a.md", "index.md")[:2] == (True, "concepts/a.md")
    assert nav.open("b.md", "concepts/a.md")[:2] == (True, "concepts/b.md")
    assert nav.open("concepts/c.md")[0] is True
    assert nav.open("/concepts/hidden.md")[0] is False       # exists, but nothing links to it


def test_a_linked_page_that_does_not_exist_is_a_broken_link(tmp_path):
    nav = wiki_nav.Navigator(_bundle(tmp_path))
    nav.open("index.md")
    ok, path, text = nav.open("/concepts/gone.md")
    assert not ok and path == "concepts/gone.md" and "broken link" in text


def test_links_cannot_climb_out_of_the_bundle():
    assert wiki_nav.normalise("../../etc/passwd", "concepts/a.md") is None
    assert wiki_nav.normalise("https://example.org/x") is None
    assert wiki_nav.normalise("/concepts/a.md#defs") == "concepts/a.md"


def test_the_server_speaks_mcp_and_logs_every_attempt(tmp_path):
    (tmp_path / "b").mkdir()
    bundle, log = _bundle(tmp_path / "b"), tmp_path / "nav.jsonl"
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "open_page", "arguments": {"link": "/concepts/b.md"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "open_page", "arguments": {"link": "index.md"}}}]
    out = io.StringIO()
    wiki_nav.serve(bundle, log, stdin=io.StringIO("\n".join(json.dumps(r) for r in reqs)), stdout=out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]           # the notification got no reply
    assert replies[1]["result"]["tools"][0]["name"] == "open_page"
    assert replies[2]["result"]["isError"] is True and replies[3]["result"]["isError"] is False
    logged = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["path"], r["ok"]) for r in logged] == [("concepts/b.md", False), ("index.md", True)]
