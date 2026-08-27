from __future__ import annotations

from coscience import wiki_lint, wiki_okf


def _page(path, title, **kw):
    return wiki_okf.Page(path=path, type="Concept", title=title,
                         body="# Definition\n\n" + "x" * 300, **kw)


def _rules(findings):
    return [f.rule for f in findings]


def test_a_merged_page_is_flagged_until_its_prose_is_rewritten():
    pages = [_page("concepts/a.md", "A", extra={"merged_from": ["b"]})]
    findings = wiki_lint.lint(pages)
    assert "page/unmerged-prose" in _rules(findings)


def test_the_finding_names_what_was_merged_in():
    pages = [_page("concepts/a.md", "A", extra={"merged_from": ["b", "c"]})]
    found = [f for f in wiki_lint.lint(pages) if f.rule == "page/unmerged-prose"]
    assert "b" in found[0].message and "c" in found[0].message


def test_a_page_with_no_marker_is_not_flagged():
    assert "page/unmerged-prose" not in _rules(wiki_lint.lint([
        _page("concepts/a.md", "A")]))


def test_an_empty_marker_is_not_flagged():
    """Clearing the marker is how an agent says the prose pass is done."""
    assert "page/unmerged-prose" not in _rules(wiki_lint.lint([
        _page("concepts/a.md", "A", extra={"merged_from": []})]))


def test_a_malformed_marker_does_not_raise():
    """OKF parsers never raise. An agent writing a string here is a bad page,
    not a crashed lint run."""
    findings = wiki_lint.lint([
        _page("concepts/a.md", "A", extra={"merged_from": "b"})])
    assert "page/unmerged-prose" in _rules(findings)


def test_the_rule_is_a_warning_not_an_error():
    """Stacked prose is ugly, not broken. An error here would make every merge
    fail the health badge until an agent happened to run."""
    found = [f for f in wiki_lint.lint([
        _page("concepts/a.md", "A", extra={"merged_from": ["b"]})])
        if f.rule == "page/unmerged-prose"]
    assert found[0].severity == "warn"
