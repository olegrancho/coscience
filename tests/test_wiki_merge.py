from __future__ import annotations

import pytest

from coscience import wiki_merge, wiki_okf


def _page(path, title, body="", **kw):
    return wiki_okf.Page(path=path, type=kw.pop("type", "Concept"), title=title,
                         body=body, **kw)


def test_sections_are_stacked_under_the_winners_headings():
    winner = _page("concepts/a.md", "A", "# Definition\n\nA is a thing.\n")
    loser = _page("concepts/b.md", "B", "# Definition\n\nB is the same thing.\n")
    out = wiki_merge.plan(winner, loser, [])
    definition = out.winner.section("Definition")
    assert "A is a thing." in definition
    assert "B is the same thing." in definition


def test_a_section_only_the_loser_has_is_carried_over():
    winner = _page("concepts/a.md", "A", "# Definition\n\nA.\n")
    loser = _page("concepts/b.md", "B", "# Definition\n\nB.\n\n# Evidence\n\n- b1\n")
    out = wiki_merge.plan(winner, loser, [])
    assert "- b1" in out.winner.section("Evidence")


def test_human_notes_from_both_survive_and_say_where_they_came_from():
    """Spec 9.1: notes are the human's own words. Losing them is the one thing
    the protected section exists to prevent."""
    winner = _page("concepts/a.md", "A", "# Human notes\n\nkeep me\n")
    loser = _page("concepts/b.md", "B", "# Human notes\n\nkeep me too\n")
    out = wiki_merge.plan(winner, loser, [])
    notes = out.winner.section("Human notes")
    assert "keep me" in notes and "keep me too" in notes
    assert "b" in notes.lower()          # labelled with its origin page


def test_verification_is_cleared():
    """A verification is a claim about specific text, and the text just changed."""
    winner = _page("concepts/a.md", "A", verified=[{"by": "human:x", "at": 1.0}])
    loser = _page("concepts/b.md", "B", verified=[{"by": "human:y", "at": 2.0}])
    assert wiki_merge.plan(winner, loser, []).winner.verified == []


def test_relations_are_unioned_and_deduped():
    r = wiki_okf.Relation(type="part_of", target="/concepts/c.md", source="s1")
    winner = _page("concepts/a.md", "A", relations=[r])
    loser = _page("concepts/b.md", "B", relations=[
        wiki_okf.Relation(type="part_of", target="/concepts/c.md", source="s2"),
        wiki_okf.Relation(type="requires", target="/concepts/d.md", source="s3")])
    rels = wiki_merge.plan(winner, loser, []).winner.relations
    assert len(rels) == 2
    assert {(x.type, x.target) for x in rels} == {
        ("part_of", "/concepts/c.md"), ("requires", "/concepts/d.md")}


def test_a_relation_from_the_winner_to_the_loser_is_dropped():
    """After the merge the loser is the winner. A page cannot relate to itself."""
    winner = _page("concepts/a.md", "A", relations=[
        wiki_okf.Relation(type="refines", target="/concepts/b.md", source="s")])
    out = wiki_merge.plan(winner, _page("concepts/b.md", "B"), [])
    assert out.winner.relations == []


def test_sources_are_unioned_and_deduped_by_id():
    s = wiki_okf.Source(id="c1", resource="/sources/result-r1.md")
    winner = _page("concepts/a.md", "A", sources=[s])
    loser = _page("concepts/b.md", "B", sources=[
        wiki_okf.Source(id="c1", resource="/sources/result-r1.md"),
        wiki_okf.Source(id="c2", resource="/sources/result-r2.md")])
    assert [x.id for x in wiki_merge.plan(winner, loser, []).winner.sources] == ["c1", "c2"]


def test_the_losers_title_and_slug_become_aliases():
    """The old name must still find the page, or the merge loses discoverability."""
    winner = _page("concepts/a.md", "A", aliases=["ay"])
    loser = _page("concepts/b.md", "Bee", aliases=["bee-alias"])
    aliases = wiki_merge.plan(winner, loser, []).winner.aliases
    assert set(aliases) >= {"ay", "Bee", "b", "bee-alias"}
    assert len(aliases) == len(set(aliases))


def test_the_winner_is_marked_as_needing_a_prose_pass():
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [])
    assert out.winner.extra["merged_from"] == ["b"]


def test_merging_twice_accumulates_the_marker():
    winner = _page("concepts/a.md", "A", extra={"merged_from": ["b"]})
    out = wiki_merge.plan(winner, _page("concepts/c.md", "C"), [])
    assert out.winner.extra["merged_from"] == ["b", "c"]


def test_other_pages_have_their_links_rewritten():
    other = _page("concepts/z.md", "Z",
                  "See [B](/concepts/b.md) and [[b]] and [B again](concepts/b.md).")
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    body = out.rewritten[0].body
    assert "/concepts/b.md" not in body and "[[b]]" not in body
    assert body.count("/concepts/a.md") == 2 and "[[a]]" in body


def test_other_pages_have_their_relations_retargeted_not_dropped():
    """Spec 9.1: delete_wiki_page drops these. A merge must not — dropping them
    discards exactly the knowledge the merge exists to preserve."""
    other = _page("concepts/z.md", "Z", relations=[
        wiki_okf.Relation(type="requires", target="/concepts/b.md", source="s")])
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    assert [(r.type, r.target) for r in out.rewritten[0].relations] == [
        ("requires", "/concepts/a.md")]


def test_retargeting_that_creates_a_duplicate_relation_dedupes():
    other = _page("concepts/z.md", "Z", relations=[
        wiki_okf.Relation(type="requires", target="/concepts/a.md", source="s"),
        wiki_okf.Relation(type="requires", target="/concepts/b.md", source="s")])
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    assert len(out.rewritten[0].relations) == 1


def test_a_page_that_never_mentioned_the_loser_is_not_rewritten():
    """rewritten is what the caller must write back. Listing untouched pages
    would make every merge commit touch the whole bundle."""
    other = _page("concepts/z.md", "Z", "nothing to see")
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    assert out.rewritten == []


def test_the_loser_path_is_reported_for_the_caller_to_delete():
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [])
    assert out.loser_path == "concepts/b.md"


def test_refuses_a_source_page():
    """Spec 9.1: a source page is bound to a real object by origin_hash. Merging
    two would make src/hash-drift and src/missing meaningless."""
    with pytest.raises(ValueError):
        wiki_merge.plan(_page("concepts/a.md", "A"),
                        _page("sources/result-r1.md", "R1", type="Source"), [])
    with pytest.raises(ValueError):
        wiki_merge.plan(_page("sources/result-r1.md", "R1", type="Source"),
                        _page("concepts/a.md", "A"), [])


def test_refuses_merging_a_page_into_itself():
    with pytest.raises(ValueError):
        wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/a.md", "A"), [])


def test_plan_does_not_mutate_the_caller_s_pages():
    """The module is declared pure. A caller that reads winner/loser/others after
    calling plan() must not silently see rewritten state — plan() must build new
    objects, never mutate the ones it was handed."""
    winner = _page("concepts/a.md", "A", "# Definition\n\nA.\n",
                    verified=[{"by": "human:x", "at": 1.0}],
                    relations=[wiki_okf.Relation(type="refines", target="/concepts/b.md",
                                                 source="s")])
    loser = _page("concepts/b.md", "B", "# Definition\n\nB.\n")
    other = _page("concepts/z.md", "Z", "See [B](/concepts/b.md).",
                  relations=[wiki_okf.Relation(type="requires", target="/concepts/b.md",
                                               source="s")])
    winner_snapshot = _page("concepts/a.md", "A", "# Definition\n\nA.\n",
                            verified=[{"by": "human:x", "at": 1.0}],
                            relations=[wiki_okf.Relation(type="refines", target="/concepts/b.md",
                                                         source="s")])
    other_snapshot_body = other.body
    other_snapshot_relations = list(other.relations)

    out = wiki_merge.plan(winner, loser, [other])

    assert winner.body == winner_snapshot.body
    assert winner.verified == winner_snapshot.verified
    assert [(r.type, r.target) for r in winner.relations] == [
        (r.type, r.target) for r in winner_snapshot.relations]
    assert other.body == other_snapshot_body
    assert [(r.type, r.target) for r in other.relations] == [
        (r.type, r.target) for r in other_snapshot_relations]
    assert out.winner is not winner
    assert out.rewritten[0] is not other
