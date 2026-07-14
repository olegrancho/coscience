from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import gather_context, pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience import threads as _threads


def _c(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return TestClient(build_app(svc)), svc


def test_guidance_thread_roundtrip(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    c = TestClient(build_app(svc))
    r = c.post("/api/programs/p1/guidance", json={"text": "prefer cheap models"})
    assert r.status_code == 201
    gs = c.get("/api/programs/p1/guidance").json()
    assert gs[0]["messages"][0]["text"] == "prefer cheap models"


def test_guidance_comment_starts_thread_and_completes(tmp_path):
    c, svc = _c(tmp_path)
    r = c.post("/api/programs/p1/guidance", json={"text": "focus on assays"})
    assert r.status_code == 201
    assert r.json()["messages"][0]["text"] == "focus on assays"
    assert r.json()["target"] == "pm"
    pub = c.get("/api/programs/p1/guidance").json()[0]
    assert pub["messages"][0]["text"] == "focus on assays"
    tid = pub["id"]
    assert c.post(f"/api/programs/p1/guidance/{tid}/complete").status_code == 200
    assert c.get("/api/programs/p1/guidance").json()[0]["status"] == "complete"


def test_guidance_thread_append_reopens_and_seen(tmp_path):
    c, svc = _c(tmp_path)
    tid = c.post("/api/programs/p1/guidance", json={"text": "first"}).json()["id"]
    # simulate a PM reply landing on the thread
    guidance_threads = svc.substrate.load_guidance("p1")
    _threads.append(guidance_threads[0], "pm", "noted", "", now=2.0)
    svc.substrate.save_guidance("p1", guidance_threads)
    got = c.get("/api/programs/p1/guidance").json()[0]
    assert got["agent_unseen"] is True
    assert c.post(f"/api/programs/p1/guidance/{tid}/seen").status_code == 200
    got = c.get("/api/programs/p1/guidance").json()[0]
    assert got["agent_unseen"] is False

    c.post(f"/api/programs/p1/guidance/{tid}/complete")
    r = c.post("/api/programs/p1/guidance", json={"text": "more", "thread_id": tid})
    assert r.status_code == 201
    got = c.get("/api/programs/p1/guidance").json()[0]
    assert len(got["messages"]) == 3
    assert got["status"] == "open"          # reopened by the new human message


def test_guidance_thread_not_found(tmp_path):
    c, svc = _c(tmp_path)
    assert c.post("/api/programs/p1/guidance/ghost/complete").status_code == 404
    assert c.post("/api/programs/p1/guidance/ghost/seen").status_code == 404
    assert c.post("/api/programs/p1/guidance", json={"text": "x", "thread_id": "ghost"}).status_code == 404


# --- PM-side: gather_context surfacing + the thread-reply apply loop ---

def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_gather_context_surfaces_open_guidance_feedback(substrate):
    _prog(substrate)
    th = _threads.new_thread("pm", "prefer cheap models", "stroganov", now=1.0)
    substrate.save_guidance("p1", [th])

    ctx = gather_context(substrate, "p1")
    assert len(ctx.guidance_feedback) == 1
    assert ctx.guidance_feedback[0]["thread_id"] == th["id"]
    assert ctx.guidance_feedback[0]["messages"][-1] == {"role": "human", "text": "prefer cheap models"}
    assert ctx.human_guidance == ["prefer cheap models"]


def test_gather_context_excludes_completed_guidance_threads_from_feedback(substrate):
    _prog(substrate)
    th = _threads.new_thread("pm", "done with this", "u", now=1.0)
    th["status"] = "complete"
    substrate.save_guidance("p1", [th])
    ctx = gather_context(substrate, "p1")
    assert ctx.guidance_feedback == []
    assert ctx.human_guidance == ["done with this"]     # still standing context


def test_pm_reply_appended_to_guidance_thread(substrate):
    _prog(substrate)
    th = _threads.new_thread("pm", "prefer cheap models", "u", now=1.0)
    substrate.save_guidance("p1", [th])

    out = PMCycleOutput(report="r",
                        thread_replies=[{"thread_id": th["id"], "text": "noted, will use cheap models"}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    got = substrate.load_guidance("p1")[0]
    assert got["messages"][-1]["role"] == "pm"
    assert got["messages"][-1]["text"] == "noted, will use cheap models"
    assert got["agent_unseen"] is True

    # Closed to further PM replies until the human speaks again.
    ctx2 = gather_context(substrate, "p1")
    assert ctx2.guidance_feedback == []


def test_single_reply_map_resolves_across_sprint_idea_and_guidance_threads(substrate):
    # The reply map from thread_replies isn't tagged with which surface it
    # belongs to — the apply loop must try each surface and land each reply on
    # whichever one actually owns the id.
    _prog(substrate)
    sprint_th = _threads.new_thread("pm", "change to cpu", "u", now=1.0)
    s = Sprint(id="p1-s", status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1")
    s.threads.append(sprint_th)
    substrate.save_sprint(s)

    guidance_th = _threads.new_thread("pm", "prefer cheap models", "u", now=1.0)
    substrate.save_guidance("p1", [guidance_th])

    out = PMCycleOutput(report="r", thread_replies=[
        {"thread_id": sprint_th["id"], "text": "done — set cpu"},
        {"thread_id": guidance_th["id"], "text": "acknowledged"},
    ])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    assert substrate.load_sprint("p1-s").threads[0]["messages"][-1]["text"] == "done — set cpu"
    assert substrate.load_guidance("p1")[0]["messages"][-1]["text"] == "acknowledged"


def test_pm_reply_ignores_unmatched_guidance_thread(substrate):
    _prog(substrate)
    substrate.save_guidance("p1", [])
    out = PMCycleOutput(report="r", thread_replies=[{"thread_id": "nope", "text": "ignored"}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    assert substrate.load_guidance("p1") == []


def test_new_guidance_message_changes_fingerprint(substrate):
    # A new human message on an open guidance thread must re-trigger the PM (the
    # fingerprint keys guidance on thread_id + last-human-text).
    from coscience.pm_agent import context_fingerprint
    _prog(substrate)
    th = _threads.new_thread("pm", "prefer cheap models", "u", now=1.0)
    substrate.save_guidance("p1", [th])
    fp1 = context_fingerprint(gather_context(substrate, "p1"))
    _threads.append(th, "human", "actually, prefer opus for hard tasks", "u", now=2.0)
    substrate.save_guidance("p1", [th])
    fp2 = context_fingerprint(gather_context(substrate, "p1"))
    assert fp1 != fp2
