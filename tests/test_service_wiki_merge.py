from __future__ import annotations

import pytest

from coscience import wiki_okf, wiki_store
from coscience.models import Program
from coscience.service import NotFoundError, Service


def _seed(substrate):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title, body in (
            ("concepts/a.md", "A", "# Definition\n\nA is a thing.\n"),
            ("concepts/b.md", "B", "# Definition\n\nB is the same thing.\n"),
            ("concepts/z.md", "Z", "See [B](/concepts/b.md).\n")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title, body=body))


def test_the_merge_writes_the_winner_and_removes_the_loser(substrate):
    _seed(substrate)
    out = Service(substrate.repo_root).merge_wiki_pages(
        "p1", "concepts/a.md", "concepts/b.md")
    assert out["loser"] == "concepts/b.md"
    winner = wiki_store.read_page(substrate, "p1", "concepts/a.md")
    assert "B is the same thing." in winner.section("Definition")
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is None


def test_pages_pointing_at_the_loser_are_rewritten(substrate):
    _seed(substrate)
    out = Service(substrate.repo_root).merge_wiki_pages(
        "p1", "concepts/a.md", "concepts/b.md")
    assert out["rewritten"] == ["concepts/z.md"]
    assert "/concepts/a.md" in wiki_store.read_page(substrate, "p1", "concepts/z.md").body


def test_the_merge_is_its_own_commit_naming_both_pages(substrate):
    """Spec 9.1 rules that nothing gates an automatic merge but git. That makes
    this commit the undo, so it has to be findable and it has to stand alone.

    `substrate` (a bare tmp_path) is not a git repo, so Substrate.commit is a
    no-op — same reason test_wiki_lint_sources.py's previous_bodies test makes
    one. Init it here so the merge's commit actually lands and is checkable."""
    import subprocess
    root = str(substrate.repo_root)
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)
    _seed(substrate)
    Service(substrate.repo_root).merge_wiki_pages("p1", "concepts/a.md", "concepts/b.md")
    subject = subprocess.run(["git", "log", "-1", "--format=%s"],
                             cwd=substrate.repo_root, capture_output=True,
                             text=True).stdout.strip()
    assert "merge" in subject.lower()
    assert "concepts/a.md" in subject and "concepts/b.md" in subject


def test_the_merge_reports_the_commit_it_made(substrate):
    """git is the only guard on an automatic merge, so the SHA is the undo."""
    import subprocess
    _seed(substrate)
    subprocess.run(["git", "init", "-q", str(substrate.repo_root)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(substrate.repo_root), "config", k, v], check=True)
    out = Service(substrate.repo_root).merge_wiki_pages(
        "p1", "concepts/a.md", "concepts/b.md")
    head = subprocess.run(["git", "-C", str(substrate.repo_root), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert out["commit"] == head


def test_a_missing_page_is_not_found(substrate):
    _seed(substrate)
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).merge_wiki_pages(
            "p1", "concepts/a.md", "concepts/ghost.md")


def test_a_source_page_is_refused(substrate):
    _seed(substrate)
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1"))
    with pytest.raises(ValueError):
        Service(substrate.repo_root).merge_wiki_pages(
            "p1", "concepts/a.md", "sources/result-r1.md")


def test_a_traversing_path_is_not_found(substrate):
    """Same containment guard as every other {slug:path} route. A page path
    arrives from a URL and is a traversal primitive."""
    _seed(substrate)
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).merge_wiki_pages(
            "p1", "concepts/a.md", "../../../etc/passwd")


def test_accepting_a_merge_records_it_in_the_activity_trail(substrate):
    """Ruled 2026-08-28: Activity shows everything that changed the wiki, not
    only what runs did. The entry must say a human did it."""
    _seed(substrate)
    merge_id = "m0001"
    winner = "concepts/a.md"
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merge_proposals"] = [
            {"id": merge_id, "winner": winner, "loser": "concepts/b.md",
             "why": "same idea", "run": "r0001", "at": 1.0}]
    svc = Service(substrate.repo_root)
    svc.accept_wiki_merge("p1", merge_id)
    state = wiki_store.load_state(substrate, "p1")
    entry = state["runs"][0]
    assert entry["kind"] == "merge"
    assert entry["by"] == "human"
    assert entry["merged"] and entry["merged"][0]["winner"] == winner
