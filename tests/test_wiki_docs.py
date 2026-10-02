"""Program documentation as a wiki source (todo L12): a person ticks workdir files in
Wiki settings; ticked files are ingested, re-ingested when edited, cited, and swept."""
from __future__ import annotations

import pytest

from coscience import wiki_lint, wiki_okf, wiki_prompts, wiki_read, wiki_store
from coscience.models import Program
from coscience.service import Service


def _program(substrate, tmp_path, docs=()):
    work = tmp_path / "work"
    (work / "docs").mkdir(parents=True)
    (work / "README.md").write_text("# Kernel scorer\n\nThree GraphConv layers.\n")
    (work / "docs" / "data.md").write_text("data\n")
    (work / "docs" / "deep").mkdir()
    (work / "docs" / "deep" / "x.md").write_text("too deep\n")
    (work / ".pytest_cache").mkdir()
    (work / ".pytest_cache" / "README.md").write_text("cache\n")
    substrate.save_program(Program(id="p1", title="P", goals="g", workdir=str(work),
                                   wiki_docs=list(docs)))
    return work


def test_candidates_are_the_top_level_and_one_down_without_caches(substrate, tmp_path):
    work = _program(substrate, tmp_path)
    assert wiki_store.doc_candidates(str(work)) == ["README.md", "docs/data.md"]


def test_nothing_is_ingested_until_ticked(substrate, tmp_path):
    _program(substrate, tmp_path)
    assert not [o for o in wiki_store.program_objects(substrate, "p1") if o.kind == "doc"]


def test_a_ticked_doc_is_an_object_and_an_edit_queues_it_again(substrate, tmp_path):
    work = _program(substrate, tmp_path, docs=["README.md"])
    [doc] = [o for o in wiki_store.program_objects(substrate, "p1") if o.kind == "doc"]
    assert doc.oid == "doc:README.md"
    assert doc.slug == "sources/doc-readme.md"
    assert doc.resource == "workdir:README.md"
    assert doc.title == "Kernel scorer (README.md)"
    ingested = {doc.oid: {"hash": wiki_store.object_hash(doc)}}
    assert not wiki_store.pending_objects(substrate, "p1", ingested)
    (work / "README.md").write_text("# Kernel scorer\n\nThree GraphConv layers, five custom.\n")
    assert [o.oid for o in wiki_store.pending_objects(substrate, "p1", ingested)] == ["doc:README.md"]


def test_a_path_outside_the_workdir_is_never_read(substrate, tmp_path):
    work = _program(substrate, tmp_path, docs=["../secret.md"])
    (tmp_path / "secret.md").write_text("no\n")
    assert wiki_store.doc_path(str(work), "../secret.md") is None
    assert not [o for o in wiki_store.program_objects(substrate, "p1") if o.kind == "doc"]


def _doc_page(rel):
    return wiki_okf.Page(path=wiki_store.doc_slug(rel), type="Source", title=rel,
                         body="x" * 400, extra={"origin": f"doc:{rel}", "origin_hash": ""})


def test_unticking_a_doc_retires_its_page_and_deleting_it_makes_it_missing(substrate, tmp_path):
    work = _program(substrate, tmp_path, docs=[])
    pages = [_doc_page("README.md"), _doc_page("gone.md")]
    retired = wiki_store.retired_docs(substrate, "p1", pages)
    assert retired == {"doc:README.md": ""}
    found = {(f.rule, f.path) for f in wiki_lint._source_rules(pages, {}, retired)}
    assert ("src/superseded", "sources/doc-readme.md") in found
    assert ("src/missing", "sources/doc-gone.md") in found
    assert (work / "README.md").exists()


def test_settings_list_tick_and_refuse_escapes(substrate, tmp_path):
    _program(substrate, tmp_path)
    svc = Service(substrate.repo_root)
    files = svc.wiki_docs("p1")["files"]
    assert [f["path"] for f in files] == ["README.md", "docs/data.md"]
    assert not any(f["selected"] for f in files)
    out = svc.set_wiki_docs("p1", ["README.md", "README.md"])
    assert [f["path"] for f in out["files"] if f["selected"]] == ["README.md"]
    assert substrate.load_program("p1").wiki_docs == ["README.md"]
    with pytest.raises(ValueError):
        svc.set_wiki_docs("p1", ["../etc.md"])
    with pytest.raises(ValueError):
        svc.set_wiki_docs("p1", ["nope.md"])


def test_a_ticked_file_that_vanished_stays_listed_so_it_can_be_unticked(substrate, tmp_path):
    work = _program(substrate, tmp_path, docs=["README.md"])
    (work / "README.md").unlink()
    files = Service(substrate.repo_root).wiki_docs("p1")["files"]
    assert {"path": "README.md", "selected": True, "ingested": False, "missing": True} in files


def test_doc_citations_link_to_the_wiki_page_that_summarises_the_file():
    ref = wiki_read.provenance_ref("d1", "workdir:docs/data.md", "p1")
    assert (ref["kind"], ref["href"]) == ("doc", "/programs/p1/wiki/sources/doc-docs-data")
    ref = wiki_read.provenance_ref("d1", "/sources/doc-readme.md", "p1")
    assert (ref["kind"], ref["href"]) == ("doc", "/programs/p1/wiki/sources/doc-readme")


def test_the_ingest_says_what_a_doc_is_and_the_sweep_reads_the_ticked_ones(substrate, tmp_path):
    work = _program(substrate, tmp_path, docs=["README.md"])
    program = substrate.load_program("p1")
    bundle = wiki_store.ensure_bundle(substrate, "p1")
    text = wiki_prompts.render_ingest(program, bundle, [], tmp_path / "run")
    assert "documentation object (`doc:<path>`)" in text
    sweep = wiki_prompts.render_sweep(program, bundle, tmp_path / "run")
    assert str((work / "README.md").resolve()) in sweep


def test_an_alias_shared_by_a_topic_and_a_background_page_names_its_owner():
    pages = [wiki_okf.Page(path="concepts/kernel.md", type="Concept", title="Kernel",
                           aliases=["rbfu"], body="x" * 400),
             wiki_okf.Page(path="entities/model.md", type="Entity", title="Model",
                           aliases=["rbfu"], body="x" * 400)]
    [f] = [f for f in wiki_lint._page_rules(pages, "", None) if f.rule == "page/near-duplicate"]
    assert "keep it on the topic concepts/kernel.md" in f.message
    assert "from entities/model.md" in f.message
