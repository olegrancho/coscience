import time

from coscience import wiki_lint, wiki_okf


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 400 + "\n")
    return wiki_okf.Page(path=path, **kw)


def _rules(findings, prefix=""):
    return sorted({f.rule for f in findings if f.rule.startswith(prefix)})


def test_bad_yaml_is_an_error():
    page = wiki_okf.parse_page("concepts/a.md", "---\ntype: [oops\n---\n\nbody\n")
    findings = wiki_lint.lint([page])
    assert "okf/bad-yaml" in _rules(findings)
    bad = [f for f in findings if f.rule == "okf/bad-yaml"][0]
    assert bad.severity == "error"
    assert bad.path == "concepts/a.md"


def test_missing_type_is_an_error():
    findings = wiki_lint.lint([_page("concepts/a.md", type="")])
    assert "okf/missing-type" in _rules(findings)


def test_unknown_type_is_tolerated_not_flagged():
    findings = wiki_lint.lint([_page("concepts/a.md", type="Protocol")])
    assert _rules(findings, "okf/") == []


def test_stub_page_is_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", body="# Definition\n\ntiny\n")])
    assert "page/stub" in _rules(findings)
    assert [f for f in findings if f.rule == "page/stub"][0].severity == "warn"


def test_stale_after_in_the_past_is_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", stale_after="2020-01-01")],
                              now=time.time())
    assert "page/stale" in _rules(findings)


def test_stale_after_in_the_future_is_not_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", stale_after="2099-01-01")],
                              now=time.time())
    assert "page/stale" not in _rules(findings)


def test_old_generated_at_falls_back_to_the_age_heuristic():
    old = {"by": "coscience-wiki/x", "at": "2020-01-01T00:00:00Z"}
    findings = wiki_lint.lint([_page("concepts/a.md", generated=old)], now=time.time())
    assert "page/stale" in _rules(findings)


def test_orphan_page_is_info():
    findings = wiki_lint.lint([_page("concepts/a.md")], index_body="# Index\n")
    assert "page/orphan" in _rules(findings)


def test_page_linked_from_index_is_not_an_orphan():
    findings = wiki_lint.lint([_page("concepts/a.md")],
                              index_body="- [a](/concepts/a.md)\n")
    assert "page/orphan" not in _rules(findings)


def test_page_linked_from_another_page_is_not_an_orphan():
    linker = _page("concepts/b.md", body="# Definition\n\nsee [a](/concepts/a.md)\n" + "x" * 400)
    findings = wiki_lint.lint([_page("concepts/a.md"), linker])
    assert not [f for f in findings if f.rule == "page/orphan" and f.path == "concepts/a.md"]


def test_duplicate_slug_across_directories_is_an_error():
    findings = wiki_lint.lint([_page("concepts/a.md"), _page("entities/a.md", type="Entity")])
    assert "page/duplicate-slug" in _rules(findings)


def test_near_duplicate_titles_are_warned():
    findings = wiki_lint.lint([
        _page("concepts/a.md", title="Template replication takeoff"),
        _page("concepts/b.md", title="Template replication takeoff ")])
    assert "page/near-duplicate" in _rules(findings)


def test_alias_colliding_with_another_title_is_a_near_duplicate():
    findings = wiki_lint.lint([
        _page("concepts/a.md", title="Takeoff regime"),
        _page("concepts/b.md", title="Something else", aliases=["takeoff regime"])])
    assert "page/near-duplicate" in _rules(findings)


def test_index_frontmatter_rule_only_fires_on_a_non_root_index():
    findings = wiki_lint.lint([_page("concepts/index.md")])
    assert "okf/index-frontmatter" in _rules(findings)


def test_findings_are_sorted_errors_first():
    findings = wiki_lint.lint([
        _page("concepts/a.md", type="", body="tiny"),
    ])
    assert findings[0].severity == "error"


def test_render_report_is_readable_and_groups_by_rule():
    findings = wiki_lint.lint([_page("concepts/a.md", type="", body="tiny")])
    text = wiki_lint.render_report(findings)
    assert "okf/missing-type" in text
    assert "concepts/a.md" in text
    assert "error" in text


def test_render_report_on_a_clean_wiki():
    assert "no findings" in wiki_lint.render_report([]).lower()


def test_counts():
    findings = wiki_lint.lint([_page("concepts/a.md", type="", body="tiny")])
    c = wiki_lint.counts(findings)
    assert c["error"] >= 1
    assert set(c) == {"error", "warn", "info"}


def test_lint_never_raises_on_junk():
    junk = wiki_okf.Page(path="concepts/x.md", type="Concept", tags=["a"],
                         generated={"at": 12345}, stale_after="not-a-date", body="")
    wiki_lint.lint([junk], now=time.time())        # must not raise
