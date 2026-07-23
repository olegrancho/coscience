from coscience import artifacts
from coscience.service import NotFoundError, Service


def _seed(substrate, aid="page", files=None):
    artifacts.create_artifact(substrate, "p", aid, aid, "page")
    work = artifacts.seed_work(substrate, "p", aid)
    for name, text in (files or {"index.html": "<h1>hi</h1>"}).items():
        (work / name).write_text(text)
    artifacts.cut_version(substrate, "p", aid, "human", now=1.0)


def test_read_artifact_file_text(substrate):
    _seed(substrate, "doc", {"content.md": "hello world"})
    svc = Service(substrate.repo_root)
    f = svc.read_artifact_file("p", "doc", "v1", "content.md")
    assert f["content"] == "hello world"
    assert f["binary"] is False
    assert f["size"] == 11


def test_read_artifact_file_traversal_blocked(substrate):
    _seed(substrate, "doc", {"content.md": "x"})
    svc = Service(substrate.repo_root)
    for bad in ("../../meta.md", "/etc/passwd", "../meta.md"):
        try:
            svc.read_artifact_file("p", "doc", "v1", bad)
            assert False, f"traversal not blocked: {bad}"
        except NotFoundError:
            pass


def test_artifact_page_file_returns_guarded_path(substrate):
    _seed(substrate, "site", {"index.html": "<h1>hi</h1>", "app.js": "1"})
    svc = Service(substrate.repo_root)
    p = svc.artifact_page_file("p", "site", "v1", "app.js")
    assert p.read_text() == "1"
    try:
        svc.artifact_page_file("p", "site", "v1", "../meta.md")
        assert False, "traversal not blocked"
    except NotFoundError:
        pass


def test_version_dir_missing_raises(substrate):
    _seed(substrate, "doc", {"content.md": "x"})
    svc = Service(substrate.repo_root)
    try:
        svc.artifact_version_dir("p", "doc", "v9")
        assert False
    except NotFoundError:
        pass


def test_program_or_aid_escape_blocked(substrate):
    _seed(substrate, "doc", {"content.md": "x"})
    svc = Service(substrate.repo_root)
    for bad_program, bad_aid in (("..", "doc"), ("p", ".."), ("../..", "doc")):
        try:
            svc.artifact_version_dir(bad_program, bad_aid, "v1")
            assert False, f"escape not blocked: {bad_program}/{bad_aid}"
        except NotFoundError:
            pass
