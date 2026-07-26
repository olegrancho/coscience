"""The two human-facing doors onto artifacts.adopt(): the HTTP endpoint (for the
dashboard) and the `coscience artifact add` command (for a shell on the box)."""
from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.cli import main
from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service
from coscience.substrate import Substrate


def _program(substrate, workdir=""):
    substrate.save_program(Program(id="p", title="P", goals="g", workdir=str(workdir)))


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def test_http_adopt_creates_artifact(substrate, tmp_path):
    _program(substrate, workdir=tmp_path)
    (tmp_path / "REPORT.md").write_text("# Findings\n")
    r = _client(substrate).post("/api/programs/p/artifacts", json={
        "aid": "report", "title": "Closeout dossier", "kind": "md",
        "files": ["REPORT.md"]})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Closeout dossier" and body["current"] == "v1"
    assert body["current_files"] == ["REPORT.md"]


def test_http_adopt_inline_content(substrate, tmp_path):
    _program(substrate, workdir=tmp_path)
    r = _client(substrate).post("/api/programs/p/artifacts", json={
        "aid": "stub", "title": "Stub", "content": "# Stub\n"})
    assert r.status_code == 201
    assert (substrate.artifact_dir("p", "stub") / "v1" / "stub.md").read_text() == "# Stub\n"


def test_http_adopt_rejects_path_escape_422(substrate, tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    _program(substrate, workdir=inside)
    (tmp_path / "secret.md").write_text("nope")
    r = _client(substrate).post("/api/programs/p/artifacts", json={
        "aid": "leak", "files": ["../secret.md"]})
    assert r.status_code == 422
    assert not (substrate.artifact_dir("p", "leak") / "meta.md").is_file()


def test_http_adopt_conflict_when_busy(substrate, tmp_path):
    _program(substrate, workdir=tmp_path)
    (tmp_path / "doc.md").write_text("x")
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", 1.0)
    r = _client(substrate).post("/api/programs/p/artifacts", json={
        "aid": "doc", "files": ["doc.md"]})
    assert r.status_code == 409


def test_http_adopt_unknown_program_404(substrate, tmp_path):
    r = _client(substrate).post("/api/programs/nope/artifacts", json={
        "aid": "x", "content": "y"})
    assert r.status_code == 404


def test_cli_artifact_add(tmp_path, capsys):
    repo, src = tmp_path / "repo", tmp_path / "src"
    src.mkdir()
    (src / "REPORT.md").write_text("# Findings\n")
    substrate = Substrate(repo)
    substrate.save_program(Program(id="p", title="P", goals="g"))
    rc = main(["artifact", "add", "--repo", str(repo), "--program", "p",
               "--id", "report", "--title", "Dossier", "--kind", "md",
               str(src / "REPORT.md")])
    assert rc == 0
    assert "p/report v1" in capsys.readouterr().out
    art = Substrate(repo).load_artifact("p", "report")
    assert art.title == "Dossier" and art.current == "v1"
