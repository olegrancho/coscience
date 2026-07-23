from coscience import artifacts
from coscience.service import NotFoundError, Service


def _mk(substrate, aid="doc"):
    artifacts.create_artifact(substrate, "p", aid, aid, "md")


def test_add_comment_creates_pm_thread(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    t = svc.add_artifact_comment("p", "doc", "please tighten the intro", by="oleg")
    assert t["target"] == "pm"
    assert t["messages"][0]["text"] == "please tighten the intro"
    assert t["messages"][0]["by"] == "oleg"
    # persisted on the artifact
    assert len(svc.get_artifact("p", "doc")["threads"]) == 1


def test_add_comment_appends_to_thread(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    t = svc.add_artifact_comment("p", "doc", "first", by="oleg")
    t2 = svc.add_artifact_comment("p", "doc", "second", by="oleg", thread_id=t["id"])
    assert [m["text"] for m in t2["messages"]] == ["first", "second"]


def test_empty_comment_rejected(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    try:
        svc.add_artifact_comment("p", "doc", "   ")
        assert False
    except ValueError:
        pass


def test_complete_and_delete_thread(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    t = svc.add_artifact_comment("p", "doc", "note")
    assert svc.complete_artifact_thread("p", "doc", t["id"])["status"] == "complete"
    svc.delete_artifact_thread("p", "doc", t["id"])
    assert svc.get_artifact("p", "doc")["threads"] == []


def test_comment_missing_artifact_raises(substrate):
    svc = Service(substrate.repo_root)
    try:
        svc.add_artifact_comment("p", "ghost", "x")
        assert False
    except NotFoundError:
        pass
