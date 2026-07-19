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


def test_page_serve_has_csp(substrate):
    _seed(substrate, "site", {"index.html": "<h1>x</h1>"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/site/versions/v1/page/index.html")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_get_missing_404(substrate):
    c = _client(substrate)
    assert c.get("/api/programs/p/artifacts/ghost").status_code == 404
