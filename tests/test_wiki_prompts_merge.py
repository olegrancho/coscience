from __future__ import annotations

from pathlib import Path

from coscience import wiki_prompts, wiki_store
from coscience.models import Program


def _doc():
    return wiki_prompts.render_lint(
        Program(id="p1", title="P1", goals="g"), Path("/b"), "report text",
        Path("/run"))


def test_the_lint_document_defines_the_merges_shape():
    doc = _doc()
    assert '"winner"' in doc and '"loser"' in doc and '"why"' in doc


def test_the_merges_shape_uses_page_paths_not_slugs():
    """A slug is ambiguous across directories; page/duplicate-slug exists because
    of it. The consumer resolves paths."""
    assert "concepts/" in _doc().split('"merges"', 1)[1][:400]


def test_the_lint_document_asks_for_the_prose_pass():
    doc = _doc()
    assert "merged_from" in doc
    assert "unmerged-prose" in doc or "one voice" in doc


def test_the_ingest_document_also_defines_merges():
    """A lint run is not the only run that can notice a duplicate."""
    doc = wiki_prompts.render_ingest(
        Program(id="p1", title="P1", goals="g"), Path("/b"), [], Path("/run"))
    assert '"merges"' in doc


def test_the_bundle_template_defines_merges_too(substrate):
    """The bundle's own CLAUDE.md is binding on the agent and is what it reads
    when the instruction document is not in front of it."""
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    text = (wiki_store.bundle_dir(substrate, "p1") / "CLAUDE.md").read_text()
    assert '"winner"' in text and '"loser"' in text
