from coscience import wiki_lint, wiki_okf


def _page(path, body="", relations=(), **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    return wiki_okf.Page(path=path, body=body or ("x" * 400),
                         relations=[wiki_okf.Relation(**r) for r in relations], **kw)


def _rules(findings, prefix=""):
    return sorted({f.rule for f in findings if f.rule.startswith(prefix)})


def test_wikilink_is_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", body="see [[hydrolysis-rate]] " + "x" * 400)])
    assert "link/wikilink" in _rules(findings)


def test_broken_link_is_a_warning_never_an_error():
    findings = wiki_lint.lint([_page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400)])
    broken = [f for f in findings if f.rule == "link/broken"]
    assert broken and broken[0].severity == "warn"


def test_external_links_are_not_broken():
    findings = wiki_lint.lint([_page("concepts/a.md", body="see [x](https://x.test) " + "x" * 400)])
    assert "link/broken" not in _rules(findings)


def test_unknown_relation_type_is_an_error():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "relates_to", "target": "/concepts/b.md", "source": "c1"}])])
    assert "rel/unknown-type" in _rules(findings)


def test_relation_without_a_body_link_is_the_containment_error():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])])
    rel = [f for f in findings if f.rule == "rel/no-link"]
    assert rel and rel[0].severity == "error"


def test_relation_without_a_source_is_an_error():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md"}])])
    assert "rel/no-source" in _rules(findings)


def test_relation_source_naming_an_unknown_source_id_is_an_error():
    page = _page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
                 relations=[{"type": "requires", "target": "/concepts/b.md",
                             "source": "nope"}])
    page.sources = [wiki_okf.Source(id="c1", resource="/sources/result-r1.md")]
    assert "rel/no-source" in _rules(wiki_lint.lint([page]))


def test_relation_to_a_missing_page_is_dangling():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])])
    dangling = [f for f in findings if f.rule == "rel/dangling"]
    assert dangling and dangling[0].severity == "warn"


def test_replaces_cycle_is_info():
    a = _page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
              relations=[{"type": "replaces", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", body="see [a](/concepts/a.md) " + "x" * 400,
              relations=[{"type": "replaces", "target": "/concepts/a.md", "source": "c1"}])
    findings = wiki_lint.lint([a, b])
    cycle = [f for f in findings if f.rule == "rel/cycle"]
    assert cycle and cycle[0].severity == "info"


def test_contradicts_is_not_treated_as_a_cycle():
    a = _page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
              relations=[{"type": "contradicts", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", body="see [a](/concepts/a.md) " + "x" * 400,
              relations=[{"type": "contradicts", "target": "/concepts/a.md", "source": "c1"}])
    assert "rel/cycle" not in _rules(wiki_lint.lint([a, b]))


def test_autofix_rewrites_wikilinks_into_markdown_links():
    a = _page("concepts/a.md", body="see [[hydrolysis-rate]] " + "x" * 400)
    b = _page("concepts/hydrolysis-rate.md")
    changed, fixed = wiki_lint.autofix([a, b])
    assert [p.path for p in changed] == ["concepts/a.md"]
    assert "[hydrolysis-rate](/concepts/hydrolysis-rate.md)" in changed[0].body
    assert "[[hydrolysis-rate]]" not in changed[0].body
    assert "link/wikilink" in {f.rule for f in fixed}


def test_autofix_leaves_a_wikilink_with_no_target_page_alone():
    a = _page("concepts/a.md", body="see [[nowhere]] " + "x" * 400)
    changed, _ = wiki_lint.autofix([a])
    assert changed == []


def test_autofix_appends_a_relation_link_to_satisfy_containment():
    a = _page("concepts/a.md", body="# Definition\n\n" + "x" * 400,
              relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", title="B")
    changed, fixed = wiki_lint.autofix([a, b])
    assert "(/concepts/b.md)" in changed[0].body
    assert "rel/no-link" in {f.rule for f in fixed}
    assert "rel/no-link" not in {f.rule for f in wiki_lint.lint(changed + [b])}


def test_autofix_is_idempotent():
    a = _page("concepts/a.md", body="see [[b]] " + "x" * 400)
    b = _page("concepts/b.md")
    once, _ = wiki_lint.autofix([a, b])
    twice, findings = wiki_lint.autofix(once + [b])
    assert twice == []
    assert findings == []


def test_autofixable_set():
    # okf/index-frontmatter is reported but not auto-fixed — see the comment on
    # AUTOFIXABLE for why stripping frontmatter mechanically is not worth it.
    assert wiki_lint.AUTOFIXABLE == {"link/wikilink", "rel/no-link"}


def test_wikilink_inside_a_code_fence_is_still_rewritten():
    # Phase-1 limitation: body_links and wikilinks are consistently naive and
    # do not exclude fenced code blocks. This documents the behavior.
    a = _page("concepts/a.md", body="```\nSee [[target]] in code\n```\n" + "x" * 400)
    b = _page("concepts/target.md")
    changed, fixed = wiki_lint.autofix([a, b])
    assert [p.path for p in changed] == ["concepts/a.md"]
    assert "[target](/concepts/target.md)" in changed[0].body
    assert "[[target]]" not in changed[0].body


def test_autofix_wikilink_target_ordering():
    # When a relation target appears only as a wikilink in the body, wikilinks
    # must be rewritten first. Only then can we check if the target is linked
    # and decide whether to append a # Related section.
    a = _page("concepts/a.md", body="See [[b-concept]] here " + "x" * 350,
              relations=[{"type": "requires", "target": "/concepts/b-concept.md", "source": "c1"}])
    b = _page("concepts/b-concept.md", title="B Concept")
    changed, fixed = wiki_lint.autofix([a, b])
    assert [p.path for p in changed] == ["concepts/a.md"]
    # Wikilink should be rewritten to markdown
    assert "[b-concept](/concepts/b-concept.md)" in changed[0].body
    assert "[[b-concept]]" not in changed[0].body
    # Should NOT append a # Related section since the link now exists in body
    assert "# Related" not in changed[0].body
    # No rel/no-link should be reported after autofix
    assert "rel/no-link" not in {f.rule for f in wiki_lint.lint(changed + [b])}


def test_relation_with_no_sources_block_is_an_error():
    # Regression: when a page declares no sources at all (empty sources list),
    # a relation with any source id should still error. An empty sources block
    # is not a license to cite; it is evidence the citation is bogus.
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])])
    assert "rel/no-source" in _rules(findings)


def test_autofix_dedupes_duplicate_wikilinks():
    # When the same wikilink appears twice in a body, autofix should replace
    # both occurrences but report the fix only once.
    a = _page("concepts/a.md", body="[[target]] and [[target]] again " + "x" * 350)
    b = _page("concepts/target.md")
    changed, fixed = wiki_lint.autofix([a, b])
    assert [p.path for p in changed] == ["concepts/a.md"]
    # Both occurrences should be rewritten
    assert changed[0].body.count("[target](/concepts/target.md)") == 2
    assert "[[target]]" not in changed[0].body
    # Should report exactly one link/wikilink finding
    wikilink_findings = [f for f in fixed if f.rule == "link/wikilink"]
    assert len(wikilink_findings) == 1


def test_autofix_related_section_append_is_idempotent():
    # A page fixed via the relation-containment append (# Related) must,
    # on a second autofix pass, produce no further changes and no findings.
    a = _page("concepts/a.md", body="# Definition\n\n" + "x" * 400,
              relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", title="B")
    # First pass: appends # Related
    once, fixed_once = wiki_lint.autofix([a, b])
    assert [p.path for p in once] == ["concepts/a.md"]
    assert "# Related" in once[0].body
    assert len(fixed_once) > 0
    # Second pass: no changes, no findings
    twice, fixed_twice = wiki_lint.autofix(once + [b])
    assert twice == []
    assert fixed_twice == []
