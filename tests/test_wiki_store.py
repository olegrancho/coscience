from coscience import wiki_okf, wiki_store


def test_ensure_bundle_creates_the_okf_skeleton(wiki_bundle):
    substrate, pid = wiki_bundle
    bundle = wiki_store.bundle_dir(substrate, pid)
    assert bundle == substrate.program_dir(pid) / "wiki"
    for name in ("index.md", "log.md", "CLAUDE.md", "QUESTIONS.md"):
        assert (bundle / name).is_file(), name
    for d in wiki_store.PAGE_DIRS:
        assert (bundle / d).is_dir(), d
    fm, _ = __import__("coscience.frontmatter_io", fromlist=["parse"]).parse(
        (bundle / "index.md").read_text())
    assert fm["okf_version"] == "0.2"
    assert fm["type"] == "Index"


def test_state_dir_is_a_sibling_not_a_child(wiki_bundle):
    substrate, pid = wiki_bundle
    assert wiki_store.state_dir(substrate, pid) == substrate.program_dir(pid) / ".wiki"
    assert ".wiki" not in [p.name for p in wiki_store.bundle_dir(substrate, pid).iterdir()]


def test_ensure_bundle_is_idempotent_and_never_clobbers(wiki_bundle):
    substrate, pid = wiki_bundle
    log = wiki_store.bundle_dir(substrate, pid) / "log.md"
    log.write_text("# Log\n\n- 2026-08-20 something happened\n")
    wiki_store.ensure_bundle(substrate, pid)
    assert "something happened" in log.read_text()


def test_write_then_read_page(wiki_bundle):
    substrate, pid = wiki_bundle
    page = wiki_okf.Page(path="concepts/takeoff.md", type="Concept", title="Takeoff",
                         body="# Definition\n\nSomething.\n")
    path = wiki_store.write_page(substrate, pid, page)
    assert path == wiki_store.bundle_dir(substrate, pid) / "concepts" / "takeoff.md"
    got = wiki_store.read_page(substrate, pid, "concepts/takeoff.md")
    assert got.title == "Takeoff"
    assert got.path == "concepts/takeoff.md"


def test_read_missing_page_returns_none(wiki_bundle):
    substrate, pid = wiki_bundle
    assert wiki_store.read_page(substrate, pid, "concepts/nope.md") is None


def test_iter_pages_skips_reserved_files_and_sorts(wiki_bundle):
    substrate, pid = wiki_bundle
    for slug, typ in (("b", "Concept"), ("a", "Concept")):
        wiki_store.write_page(substrate, pid, wiki_okf.Page(
            path=f"concepts/{slug}.md", type=typ, title=slug, body="x"))
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1", body="x"))
    paths = [p.path for p in wiki_store.iter_pages(substrate, pid)]
    assert paths == ["concepts/a.md", "concepts/b.md", "sources/result-r1.md"]


def test_is_empty(wiki_bundle):
    substrate, pid = wiki_bundle
    assert wiki_store.is_empty(substrate, pid) is True
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="a", body="x"))
    assert wiki_store.is_empty(substrate, pid) is False


def test_bundle_claude_md_states_the_containment_invariant(wiki_bundle):
    substrate, pid = wiki_bundle
    text = (wiki_store.bundle_dir(substrate, pid) / "CLAUDE.md").read_text()
    assert "# Human notes" in text
    assert "relations" in text
    for rel in ("requires", "contradicts", "causally_precedes"):
        assert rel in text
