import pytest

from coscience import wiki_okf, wiki_store
from coscience.service import NotFoundError, Service


def _write(substrate, path, body="x" * 400, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(path=path, body=body, **kw))


def _svc(substrate):
    return Service(substrate.repo_root)


def test_summary_and_page_list_of_a_seeded_bundle(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/a.md")
    _write(substrate, "entities/b.md", type="Entity")
    svc = _svc(substrate)
    assert svc.wiki_summary("p1")["counts"] == {"Concept": 1, "Entity": 1}
    assert sorted(p["path"] for p in svc.list_wiki_pages("p1")) == \
        ["concepts/a.md", "entities/b.md"]


def test_a_page_is_addressed_by_its_bundle_path_not_its_bare_slug(wiki_bundle):
    # Two pages can share a filename stem; the path is what disambiguates them.
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/auth-gate.md", title="Concept side")
    _write(substrate, "entities/auth-gate.md", type="Entity", title="Entity side")
    svc = _svc(substrate)
    assert svc.get_wiki_page("p1", "concepts/auth-gate")["title"] == "Concept side"
    assert svc.get_wiki_page("p1", "entities/auth-gate")["title"] == "Entity side"


def test_a_missing_page_is_a_not_found(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        _svc(substrate).get_wiki_page("p1", "concepts/nope")


@pytest.mark.parametrize("slug", [
    "../../../etc/passwd",
    "concepts/../../../../etc/passwd",
    "/etc/passwd",
    "concepts/../../programs/p1/program",
])
def test_a_traversing_slug_never_escapes_the_bundle(wiki_bundle, slug):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        _svc(substrate).wiki_page_path("p1", slug)


def test_a_symlink_out_of_the_bundle_is_refused(wiki_bundle, tmp_path):
    substrate, _ = wiki_bundle
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    link = wiki_store.bundle_dir(substrate, "p1") / "concepts" / "escape.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(NotFoundError):
        _svc(substrate).wiki_page_path("p1", "concepts/escape")


def test_search_and_log_and_lint_come_back_as_plain_data(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/lease.md", title="Compute lease")
    (wiki_store.bundle_dir(substrate, "p1") / "log.md").write_text("# Log\n\n- line\n")
    svc = _svc(substrate)
    assert [h["path"] for h in svc.search_wiki("p1", "compute")] == ["concepts/lease.md"]
    assert "- line" in svc.wiki_log("p1")
    report = svc.wiki_lint_report("p1")
    assert set(report) >= {"counts", "findings"}
    assert isinstance(report["findings"], list)


def test_summary_of_a_program_with_no_bundle_is_empty_not_an_error(substrate):
    from coscience.models import Program
    substrate.save_program(Program(id="p9", title="P9", goals="g"))
    out = _svc(substrate).wiki_summary("p9")
    assert out["pages"] == 0 and out["counts"] == {}
