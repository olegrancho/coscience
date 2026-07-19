from coscience import artifacts
from coscience.models import Program
from coscience.service import Service


def _bound_chat(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p", artifacts=["doc"])
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("draft")
    return svc, c["id"]


def test_save_cuts_a_version_and_keeps_lock(substrate):
    svc, cid = _bound_chat(substrate)
    out = svc.save_chat_version("p", cid)
    assert out == {"doc": "v1"}
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert a.lock["holder_id"] == f"chat:{cid}"     # still editing


def test_save_dedup_returns_none(substrate):
    svc, cid = _bound_chat(substrate)
    svc.save_chat_version("p", cid)                  # v1
    out = svc.save_chat_version("p", cid)            # no change -> None
    assert out == {"doc": None}


def test_delete_releases_and_cuts_final(substrate):
    svc, cid = _bound_chat(substrate)
    svc.delete_chat("p", cid)
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"          # final snapshot on release
    assert a.lock == {}              # unlocked
    assert svc.list_chats("p") == []


def test_save_unbound_raises(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p")
    try:
        svc.save_chat_version("p", c["id"])
        assert False
    except ValueError:
        pass
