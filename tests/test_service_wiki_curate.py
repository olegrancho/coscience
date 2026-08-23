import pytest

from coscience import wiki_okf, wiki_store
from coscience.service import NotFoundError, Service


def _write(substrate, path="concepts/a.md", body=None, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", "A")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path=path, body=body if body is not None else "# Definition\n\n" + "x" * 400, **kw))


def test_verify_appends_a_human_actor_and_flips_the_derived_tier(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    svc = Service(substrate.repo_root)
    assert svc.get_wiki_page("p1", "concepts/a")["trust"] == "unverified"
    out = svc.verify_wiki_page("p1", "concepts/a", by="human:oleg", now=10.0)
    assert out["trust"] == "human-reviewed"
    assert out["verified"][-1] == {"by": "human:oleg", "at": 10.0}


def test_verifying_twice_appends_rather_than_replacing(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    svc = Service(substrate.repo_root)
    svc.verify_wiki_page("p1", "concepts/a", by="human:a", now=1.0)
    out = svc.verify_wiki_page("p1", "concepts/a", by="human:b", now=2.0)
    assert [v["by"] for v in out["verified"]] == ["human:a", "human:b"]


def test_status_accepts_the_lifecycle_values_and_refuses_anything_else(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    svc = Service(substrate.repo_root)
    assert svc.set_wiki_page_status("p1", "concepts/a", "stable")["status"] == "stable"
    with pytest.raises(ValueError):
        svc.set_wiki_page_status("p1", "concepts/a", "verified")


def test_human_notes_are_written_into_the_protected_section(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    out = Service(substrate.repo_root).set_wiki_human_notes(
        "p1", "concepts/a", "the lease id changes on wake")
    assert out["human_notes"] == "the lease id changes on wake"
    assert "# Human notes" in out["body"]


def test_writing_notes_replaces_only_that_section(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, body="# Definition\n\nkeep\n\n# Human notes\n\nold\n\n"
                           "# Open questions\n\nalso keep\n")
    out = Service(substrate.repo_root).set_wiki_human_notes("p1", "concepts/a", "new")
    assert out["human_notes"] == "new"
    assert "keep" in out["body"] and "also keep" in out["body"]
    assert "old" not in out["body"]


def test_notes_can_be_added_to_a_page_that_has_no_such_section_yet(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, body="# Definition\n\nd\n")
    out = Service(substrate.repo_root).set_wiki_human_notes("p1", "concepts/a", "added")
    assert out["human_notes"] == "added"


def test_curating_preserves_unknown_frontmatter_keys(wiki_bundle):
    # OKF conformance: a round-trip must not drop what we did not recognise.
    substrate, _ = wiki_bundle
    _write(substrate, extra={"custom_key": "keep me"})
    Service(substrate.repo_root).verify_wiki_page("p1", "concepts/a", by="human:o", now=1.0)
    page = wiki_store.read_page(substrate, "p1", "concepts/a.md")
    assert page.extra.get("custom_key") == "keep me"


def test_curating_a_missing_page_is_a_not_found(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).verify_wiki_page("p1", "concepts/nope", by="human:o")
