from coscience import artifacts
from coscience.models import Program
from coscience.service import Service


def _program(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))


def test_unbound_chat_unchanged(substrate):
    _program(substrate)
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p")
    assert c["scope"] == "read"
    t = substrate.load_chat_thread("p", c["id"])
    assert t.artifacts == []


def test_bound_chat_locks_and_seeds(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p", title="edit", artifacts=["doc"])
    assert c["scope"] == "full"
    a = substrate.load_artifact("p", "doc")
    assert a.lock["holder_id"] == f"chat:{c['id']}"
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()


def test_bound_chat_rejected_when_busy(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=0.0)  # busy
    svc = Service(substrate.repo_root)
    try:
        svc.create_chat("p", artifacts=["doc"])
        assert False, "expected ValueError"
    except ValueError:
        pass
    # no orphan chat thread created
    assert svc.list_chats("p") == []
