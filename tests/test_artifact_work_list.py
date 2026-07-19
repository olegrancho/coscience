from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.http_api import build_app
from coscience.service import Service


def test_list_work_files(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)  # seeds work/
    (substrate.artifact_dir("p", "doc") / "work" / "content.md").write_text("hi")
    svc = Service(substrate.repo_root)
    assert svc.list_artifact_work_files("p", "doc") == ["content.md"]


def test_list_work_files_empty_when_no_work(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    assert svc.list_artifact_work_files("p", "doc") == []


def test_work_list_route(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "a.md").write_text("x")
    c = TestClient(build_app(Service(substrate.repo_root)))
    assert c.get("/api/programs/p/artifacts/doc/work").json() == ["a.md"]
