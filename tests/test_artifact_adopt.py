"""Lightweight artifact adoption: register files (or inline text) as an artifact
in one call — no sprint, no compute grant, no approval."""
import pytest

from coscience import artifacts


def test_adopt_creates_artifact_and_cuts_v1(substrate, tmp_path):
    src = tmp_path / "REPORT.md"
    src.write_text("# Findings\n")
    vid = artifacts.adopt(substrate, "p", "report", "Closeout dossier", "md",
                          now=1.0, created_by="human:oleg", sources=[src])
    assert vid == "v1"
    art = substrate.load_artifact("p", "report")
    assert art.title == "Closeout dossier"
    assert art.current == "v1"
    assert (substrate.artifact_dir("p", "report") / "v1" / "REPORT.md").read_text() == "# Findings\n"


def test_adopt_leaves_no_lock_and_no_workdir(substrate, tmp_path):
    src = tmp_path / "a.md"
    src.write_text("x")
    artifacts.adopt(substrate, "p", "doc", "Doc", "md", now=1.0,
                    created_by="pm", sources=[src])
    art = substrate.load_artifact("p", "doc")
    assert art.lock == {}
    assert not (substrate.artifact_dir("p", "doc") / "work").exists()


def test_adopt_from_inline_content(substrate):
    vid = artifacts.adopt(substrate, "p", "manuscript", "Draft", "md", now=1.0,
                          created_by="pm", content="# Draft\n\n_TBD_\n")
    assert vid == "v1"
    v1 = substrate.artifact_dir("p", "manuscript") / "v1"
    assert (v1 / "manuscript.md").read_text() == "# Draft\n\n_TBD_\n"


def test_adopt_inline_content_honours_filename(substrate):
    artifacts.adopt(substrate, "p", "page", "Page", "page", now=1.0,
                    created_by="pm", content="<h1>hi</h1>", filename="index.html")
    assert (substrate.artifact_dir("p", "page") / "v1" / "index.html").read_text() == "<h1>hi</h1>"


def test_adopt_copies_directory_contents_flat(substrate, tmp_path):
    d = tmp_path / "site"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<h1>hi</h1>")
    (d / "assets" / "app.css").write_text("body{}")
    artifacts.adopt(substrate, "p", "site", "Site", "page", now=1.0,
                    created_by="pm", sources=[d])
    v1 = substrate.artifact_dir("p", "site") / "v1"
    assert (v1 / "index.html").read_text() == "<h1>hi</h1>"
    assert (v1 / "assets" / "app.css").read_text() == "body{}"


def test_adopt_existing_artifact_cuts_next_version_over_current(substrate, tmp_path):
    first = tmp_path / "doc.md"
    first.write_text("one")
    artifacts.adopt(substrate, "p", "doc", "Doc", "md", now=1.0,
                    created_by="pm", sources=[first])
    other = tmp_path / "extra.md"
    other.write_text("two")
    vid = artifacts.adopt(substrate, "p", "doc", "ignored", "md", now=2.0,
                          created_by="pm", sources=[other])
    assert vid == "v2"
    v2 = substrate.artifact_dir("p", "doc") / "v2"
    assert (v2 / "doc.md").read_text() == "one"       # carried over from v1
    assert (v2 / "extra.md").read_text() == "two"     # newly adopted
    assert substrate.load_artifact("p", "doc").title == "Doc"   # title not overwritten


def test_adopt_refuses_while_another_holder_has_the_lock(substrate, tmp_path):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    assert artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:abc", 1.0)
    src = tmp_path / "doc.md"
    src.write_text("x")
    with pytest.raises(artifacts.ArtifactBusy):
        artifacts.adopt(substrate, "p", "doc", "Doc", "md", now=2.0,
                        created_by="pm", sources=[src])


def test_adopt_requires_some_content(substrate):
    with pytest.raises(ValueError):
        artifacts.adopt(substrate, "p", "empty", "Empty", "md", now=1.0,
                        created_by="pm")


def test_adopt_rejects_blank_aid(substrate, tmp_path):
    src = tmp_path / "a.md"
    src.write_text("x")
    with pytest.raises(ValueError):
        artifacts.adopt(substrate, "p", "  ", "T", "md", now=1.0,
                        created_by="pm", sources=[src])


# --- source resolution (path safety for agent-supplied names) ---

def test_resolve_sources_relative_to_base(tmp_path):
    (tmp_path / "out.md").write_text("x")
    got = artifacts.resolve_sources(tmp_path, ["out.md"])
    assert got == [tmp_path / "out.md"]


def test_resolve_sources_rejects_escape(tmp_path):
    outside = tmp_path.parent / "secret.md"
    outside.write_text("x")
    base = tmp_path / "work"
    base.mkdir()
    with pytest.raises(ValueError):
        artifacts.resolve_sources(base, ["../secret.md"])
    with pytest.raises(ValueError):
        artifacts.resolve_sources(base, [str(outside)])


def test_resolve_sources_allows_escape_when_unrestricted(tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("x")
    base = tmp_path / "work"
    base.mkdir()
    got = artifacts.resolve_sources(base, [str(outside)], restrict=False)
    assert got == [outside]


def test_resolve_sources_rejects_missing_path(tmp_path):
    with pytest.raises(ValueError):
        artifacts.resolve_sources(tmp_path, ["nope.md"])
