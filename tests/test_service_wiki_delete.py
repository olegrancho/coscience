import pytest

from coscience import wiki_okf, wiki_store
from coscience.service import NotFoundError, Service


def _write(substrate, path, relations=(), body=None):
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path=path, type="Concept", title=path.rsplit("/", 1)[-1][:-3],
        body=body if body is not None else "x" * 400,
        relations=[wiki_okf.Relation(**r) for r in relations]))


def test_delete_removes_the_page_and_reports_it(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/a.md")
    out = Service(substrate.repo_root).delete_wiki_page("p1", "concepts/a")
    assert out["deleted"] == "concepts/a.md"
    assert wiki_store.read_page(substrate, "p1", "concepts/a.md") is None


def test_delete_drops_typed_relations_pointing_at_the_dead_page(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/gone.md")
    _write(substrate, "concepts/keeper.md", relations=[
        {"type": "requires", "target": "/concepts/gone.md", "source": "c1"},
        {"type": "is_a", "target": "/concepts/other.md", "source": "c1"}])
    out = Service(substrate.repo_root).delete_wiki_page("p1", "concepts/gone")
    assert out["relations_dropped"] == [{"path": "concepts/keeper.md",
                                        "type": "requires"}]
    keeper = wiki_store.read_page(substrate, "p1", "concepts/keeper.md")
    assert [r.target for r in keeper.relations] == ["/concepts/other.md"]


def test_delete_leaves_body_links_alone(wiki_bundle):
    # A visible dangling link is honest; silently editing someone's prose is not.
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/gone.md")
    _write(substrate, "concepts/keeper.md", body="see [g](/concepts/gone.md) " + "x" * 400)
    Service(substrate.repo_root).delete_wiki_page("p1", "concepts/gone")
    keeper = wiki_store.read_page(substrate, "p1", "concepts/keeper.md")
    assert "(/concepts/gone.md)" in keeper.body


def test_deleting_a_missing_page_is_a_not_found(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).delete_wiki_page("p1", "concepts/nope")


def test_delete_cannot_be_talked_into_leaving_the_bundle(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).delete_wiki_page("p1", "../../../etc/passwd")
