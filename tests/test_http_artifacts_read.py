import io
import zipfile

from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.http_api import build_app
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate, aid, files):
    artifacts.create_artifact(substrate, "p", aid, aid, "page")
    work = artifacts.seed_work(substrate, "p", aid)
    for name, text in files.items():
        (work / name).write_text(text)
    artifacts.cut_version(substrate, "p", aid, "human", now=1.0)


def test_list_and_get(substrate):
    _seed(substrate, "doc", {"content.md": "hi"})
    c = _client(substrate)
    assert [a["id"] for a in c.get("/api/programs/p/artifacts").json()] == ["doc"]
    d = c.get("/api/programs/p/artifacts/doc").json()
    assert d["current"] == "v1"
    assert d["current_files"] == ["content.md"]


def test_read_file(substrate):
    _seed(substrate, "doc", {"content.md": "hello"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/doc/versions/v1/files/content.md")
    assert r.json()["content"] == "hello"


def test_download_single_file_raw(substrate):
    _seed(substrate, "doc", {"content.md": "hello"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/doc/versions/v1/download")
    assert r.status_code == 200
    assert r.content == b"hello"


def test_download_multi_file_zip(substrate):
    _seed(substrate, "site", {"index.html": "<h1>x</h1>", "app.js": "1"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/site/versions/v1/download")
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert set(names) == {"index.html", "app.js"}


def test_version_raw_serves_one_file_of_a_multi_file_version(substrate):
    """The reason the raw route exists: a figure stored beside its generator script
    can't come through /download, which zips anything multi-file — an <img> pointed
    at that gets application/zip and shows nothing."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    artifacts.create_artifact(substrate, "p", "fig", "fig", "figure")
    work = artifacts.seed_work(substrate, "p", "fig")
    (work / "plot.png").write_bytes(png)
    (work / "gen.py").write_text("import matplotlib")
    artifacts.cut_version(substrate, "p", "fig", "human", now=1.0)
    c = _client(substrate)

    assert c.get("/api/programs/p/artifacts/fig/versions/v1/download").headers[
        "content-type"] == "application/zip"          # what the <img> used to get
    r = c.get("/api/programs/p/artifacts/fig/versions/v1/raw/plot.png")
    assert r.status_code == 200
    assert r.content == png
    assert r.headers["content-type"] == "image/png"


def test_version_raw_rejects_traversal(substrate):
    _seed(substrate, "doc", {"content.md": "hello"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/doc/versions/v1/raw/../../artifact.md")
    assert r.status_code == 404


def test_list_artifacts_carries_thumbnail_data(substrate):
    """The overview draws a thumb per card from the list response alone."""
    artifacts.create_artifact(substrate, "p", "fig", "fig", "figure")
    work = artifacts.seed_work(substrate, "p", "fig")
    (work / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    artifacts.cut_version(substrate, "p", "fig", "human", now=1.0)
    _seed(substrate, "doc", {"content.md": "# Title\nbody text"})
    c = _client(substrate)

    rows = {a["id"]: a for a in c.get("/api/programs/p/artifacts").json()}
    assert rows["fig"]["files"] == ["plot.png"]
    assert rows["fig"]["excerpt"] == ""                # binary: nothing to quote
    assert "body text" in rows["doc"]["excerpt"]


def test_excerpt_skips_build_leftovers_and_prefers_prose(substrate):
    """A code artifact's first file by name is often a .pyc under __pycache__.
    Quoting that leaves the card blank, so unreadable candidates are passed over."""
    _seed(substrate, "code", {
        "__pycache__.pyc": "\x00\x00compiled",
        "aaa.py": "import kinetics",
        "README.md": "What this model does.",
    })
    c = _client(substrate)
    row = next(a for a in c.get("/api/programs/p/artifacts").json() if a["id"] == "code")
    assert row["excerpt"] == "What this model does."


def test_page_serve_has_csp(substrate):
    _seed(substrate, "site", {"index.html": "<h1>x</h1>"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/site/versions/v1/page/index.html")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "sandbox allow-scripts" in r.headers["content-security-policy"]


def test_get_missing_404(substrate):
    c = _client(substrate)
    assert c.get("/api/programs/p/artifacts/ghost").status_code == 404
