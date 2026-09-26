"""L8: topics is the current layout; a wiki records its own layout and keeps it."""
from coscience import wiki_layouts, wiki_prompts, wiki_store, wiki_topics
from coscience.models import Program

PROGRAM = Program(id="p1", title="T", goals="g")


def _ingest(bundle, tmp_path):
    return wiki_prompts.render_ingest(PROGRAM, bundle, [], tmp_path / "run")


def test_a_new_wiki_gets_the_topic_rules(tmp_path):
    text = _ingest(tmp_path / "wiki", tmp_path)
    assert "Current means current" in text and "Over-extract" not in text


def test_a_wiki_built_in_the_older_layout_keeps_its_rules(tmp_path):
    bundle = tmp_path / "wiki"
    bundle.mkdir()
    (bundle / "CLAUDE.md").write_text(wiki_store.BUNDLE_CLAUDE_MD)
    text = _ingest(bundle, tmp_path)
    assert "Over-extract" in text and "Current means current" not in text
    assert wiki_layouts.read(bundle) == wiki_layouts.CONCEPTS_V1


def test_the_topic_schema_carries_its_marker(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(wiki_topics.SCHEMA)
    assert wiki_topics.SCHEMA.splitlines()[0] == wiki_layouts.marker(wiki_layouts.TOPICS_V1)
    assert wiki_layouts.read(tmp_path) == wiki_layouts.TOPICS_V1
    assert wiki_layouts.read(tmp_path / "missing") is None


def test_a_marker_names_any_layout_and_version(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("<!-- coscience-wiki-layout: topics v7 -->\n# x\n")
    assert wiki_layouts.read(tmp_path) == wiki_layouts.Layout("topics", 7)
    assert wiki_layouts.next_after(wiki_layouts.Layout("topics", 7)) is None
    assert wiki_layouts.next_after(wiki_layouts.CONCEPTS_V1) == wiki_layouts.CURRENT


def test_a_new_bundle_is_written_with_the_current_schema(substrate):
    substrate.save_program(PROGRAM)
    text = (wiki_store.ensure_bundle(substrate, "p1") / "CLAUDE.md").read_text()
    assert text == wiki_layouts.schema(wiki_layouts.CURRENT)


def test_an_existing_bundle_schema_is_never_rewritten(substrate):
    substrate.save_program(PROGRAM)
    bundle = wiki_store.bundle_dir(substrate, "p1")
    bundle.mkdir(parents=True)
    (bundle / "CLAUDE.md").write_text(wiki_store.BUNDLE_CLAUDE_MD)
    assert (wiki_store.ensure_bundle(substrate, "p1") / "CLAUDE.md").read_text() == wiki_store.BUNDLE_CLAUDE_MD
