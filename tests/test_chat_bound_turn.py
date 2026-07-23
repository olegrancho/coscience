from coscience import artifacts
from coscience.models import Program
from coscience.service import Service


def _bound_chat(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p", artifacts=["doc"])
    return svc, c["id"]


def test_bound_turn_runs_in_work_dir(substrate):
    svc, cid = _bound_chat(substrate)
    calls = {}
    def fake_launch(**kw):
        calls.update(kw)
        return "tok"
    svc.post_chat_message("p", cid, "make the title bold", launch=fake_launch)
    expected = str(substrate.artifact_dir("p", "doc") / "work")
    assert calls["workdir"] == expected
    assert "doc" in calls["prompt"] or "artifact" in calls["prompt"].lower()


def test_bound_message_bumps_activity(substrate):
    svc, cid = _bound_chat(substrate)
    # advance a lot of time, then post -> last_activity moves forward
    before = substrate.load_artifact("p", "doc").lock["last_activity"]
    svc.post_chat_message("p", cid, "hi", launch=lambda **k: "tok")
    after = substrate.load_artifact("p", "doc").lock["last_activity"]
    assert after >= before


def test_bound_turn_reacquires_after_reap(substrate):
    svc, cid = _bound_chat(substrate)
    # simulate the reaper releasing the idle lock (clears lock + removes work/)
    artifacts.release_lock(substrate, "p", ["doc"], now=9999.0, created_by=f"chat:{cid}")
    assert substrate.load_artifact("p", "doc").lock == {}
    # next message must re-acquire (not crash): lock restored, work/ recreated
    svc.post_chat_message("p", cid, "keep going", launch=lambda **k: "tok")
    a = substrate.load_artifact("p", "doc")
    assert a.lock["holder_id"] == f"chat:{cid}"
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()
