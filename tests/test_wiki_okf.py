from coscience import wiki_okf

PAGE = """---
type: Concept
title: Template replication takeoff
description: The regime where template-directed replication outruns hydrolysis.
tags: [abiogenesis, kinetics]
status: draft
stale_after: 2027-02-20
generated: { by: coscience-wiki/claude-sonnet-5, at: 2026-08-20T11:04:00Z }
verified:
  - { by: 'human:oleg', at: 2026-08-21T09:00:00Z }
sources:
  - { id: c14, resource: /sources/result-x.md, title: Sprint x result, last_modified: 2026-08-14 }
relations:
  - { type: requires, target: /concepts/hydrolysis-rate.md, confidence: high, source: c14 }
aliases: [takeoff threshold]
weird_key: kept
---

# Definition

Takeoff occurs above the [hydrolysis rate](/concepts/hydrolysis-rate.md).[^c14]

# Human notes

Oleg: check the 40C case.
"""


def test_parse_page_reads_the_okf_families():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    assert p.type == "Concept"
    assert p.title == "Template replication takeoff"
    assert p.tags == ["abiogenesis", "kinetics"]
    assert p.status == "draft"
    assert p.stale_after == "2027-02-20"
    assert p.generated["by"] == "coscience-wiki/claude-sonnet-5"
    assert p.verified[0]["by"] == "human:oleg"
    assert p.sources[0].id == "c14"
    assert p.sources[0].resource == "/sources/result-x.md"
    assert p.relations[0].type == "requires"
    assert p.relations[0].target == "/concepts/hydrolysis-rate.md"
    assert p.relations[0].confidence == "high"
    assert p.relations[0].source == "c14"
    assert p.aliases == ["takeoff threshold"]


def test_unknown_keys_are_preserved():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    assert p.extra == {"weird_key": "kept"}
    assert "weird_key: kept" in wiki_okf.render_page(p)


def test_render_round_trips():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    again = wiki_okf.parse_page("concepts/takeoff.md", wiki_okf.render_page(p))
    assert wiki_okf.render_page(again) == wiki_okf.render_page(p)
    assert again.relations[0].target == p.relations[0].target


def test_sections():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    assert p.has_section("Human notes")
    assert "check the 40C case" in p.section("Human notes")
    assert p.section("Evidence") == ""


def test_bad_yaml_does_not_raise():
    p = wiki_okf.parse_page("concepts/x.md", "---\ntype: [unclosed\n---\n\nbody\n")
    assert p.bad_yaml is True
    assert p.type == ""


def test_unknown_type_is_tolerated():
    p = wiki_okf.parse_page("concepts/x.md", "---\ntype: Protocol\n---\n\nbody\n")
    assert p.type == "Protocol"
    assert p.bad_yaml is False


def test_body_links_and_wikilinks():
    body = "see [a](/concepts/a.md) and [[b-slug]] and [c](https://x.test)"
    assert wiki_okf.body_links(body) == ["/concepts/a.md", "https://x.test"]
    assert wiki_okf.wikilinks(body) == ["b-slug"]


def test_relation_vocabulary_is_frozen():
    assert "requires" in wiki_okf.RELATION_TYPES
    assert "relates_to" not in wiki_okf.RELATION_TYPES
    assert len(wiki_okf.RELATION_TYPES) == 12
