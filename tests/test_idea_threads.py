from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import gather_context, pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience import threads as _threads


def _c(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return TestClient(build_app(svc)), svc


def test_idea_comment_starts_thread_and_completes(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "an idea", source="human")
    r = c.post(f"/api/programs/p1/ideas/{idea['id']}/comments", json={"text": "refine this"})
    assert r.status_code == 201
    assert r.json()["messages"][0]["text"] == "refine this"
    assert r.json()["target"] == "pm"
    pub = c.get("/api/programs/p1/ideas").json()["ideas"][0]
    assert pub["threads"][0]["messages"][0]["text"] == "refine this"
    tid = pub["threads"][0]["id"]
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}/complete").status_code == 200
    assert c.get("/api/programs/p1/ideas").json()["ideas"][0]["threads"][0]["status"] == "complete"


def test_idea_thread_append_reopens_and_seen(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "an idea", source="pm")
    tid = c.post(f"/api/programs/p1/ideas/{idea['id']}/comments",
                 json={"text": "first"}).json()["id"]
    # simulate a PM reply landing on the thread
    _summary, ideas = svc.substrate.load_ideas("p1")
    _threads.append(ideas[0].threads[0], "pm", "done", "", now=2.0)
    svc.substrate.save_ideas("p1", _summary, ideas)
    got = c.get("/api/programs/p1/ideas").json()["ideas"][0]
    assert got["threads"][0]["agent_unseen"] is True
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}/seen").status_code == 200
    got = c.get("/api/programs/p1/ideas").json()["ideas"][0]
    assert got["threads"][0]["agent_unseen"] is False

    c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}/complete")
    r = c.post(f"/api/programs/p1/ideas/{idea['id']}/comments",
               json={"text": "more", "thread_id": tid})
    assert r.status_code == 201
    got = c.get("/api/programs/p1/ideas").json()["ideas"][0]
    assert len(got["threads"][0]["messages"]) == 3
    assert got["threads"][0]["status"] == "open"          # reopened by the new human message


def test_idea_thread_protects_and_is_pm_target(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "pm lead", source="pm")
    assert svc.list_ideas("p1")["ideas"][0]["protected"] is False
    c.post(f"/api/programs/p1/ideas/{idea['id']}/comments", json={"text": "keep it"})
    updated = svc.list_ideas("p1")["ideas"][0]
    assert updated["protected"] is True
    assert updated["threads"][0]["target"] == "pm"


def test_idea_thread_not_found(tmp_path):
    c, svc = _c(tmp_path)
    idea = svc.add_idea("p1", "an idea", source="human")
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/ghost/complete").status_code == 404
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/ghost/seen").status_code == 404
    assert c.post("/api/programs/p1/ideas/ghost/comments", json={"text": "x"}).status_code == 404


# --- PM-side: gather_context surfacing + the thread-reply apply loop ---

def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_gather_context_surfaces_open_idea_feedback(substrate):
    _prog(substrate)
    th = _threads.new_thread("pm", "pursue this", "stroganov", now=1.0)
    substrate.save_ideas("p1", "", [Idea(id="i1", text="lead", source="pm", threads=[th])])

    ctx = gather_context(substrate, "p1")
    assert len(ctx.idea_feedback) == 1
    assert ctx.idea_feedback[0]["idea_id"] == "i1"
    assert ctx.idea_feedback[0]["thread_id"] == th["id"]
    assert ctx.idea_feedback[0]["messages"][-1] == {"role": "human", "text": "pursue this"}


def test_gather_context_excludes_completed_idea_threads(substrate):
    _prog(substrate)
    th = _threads.new_thread("pm", "done with this", "u", now=1.0)
    th["status"] = "complete"
    substrate.save_ideas("p1", "", [Idea(id="i1", text="lead", source="pm", threads=[th])])
    ctx = gather_context(substrate, "p1")
    assert ctx.idea_feedback == []


def test_pm_reply_appended_to_idea_thread(substrate):
    _prog(substrate)
    th = _threads.new_thread("pm", "pursue this", "u", now=1.0)
    substrate.save_ideas("p1", "", [Idea(id="i1", text="lead", source="pm", threads=[th])])

    out = PMCycleOutput(report="r",
                        thread_replies=[{"thread_id": th["id"], "text": "promoted it"}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    _summary, ideas = substrate.load_ideas("p1")
    got = ideas[0].threads[0]
    assert got["messages"][-1]["role"] == "pm"
    assert got["messages"][-1]["text"] == "promoted it"
    assert got["agent_unseen"] is True

    # Closed to further PM replies until the human speaks again.
    ctx2 = gather_context(substrate, "p1")
    assert ctx2.idea_feedback == []


def test_single_reply_map_resolves_across_sprint_and_idea_threads(substrate):
    # The reply map from thread_replies isn't tagged with which surface it
    # belongs to — the apply loop must try sprint threads first, then idea
    # threads, and land each reply on whichever one actually owns the id.
    _prog(substrate)
    sprint_th = _threads.new_thread("pm", "change to cpu", "u", now=1.0)
    s = Sprint(id="p1-s", status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1")
    s.threads.append(sprint_th)
    substrate.save_sprint(s)

    idea_th = _threads.new_thread("pm", "pursue this", "u", now=1.0)
    substrate.save_ideas("p1", "", [Idea(id="i1", text="lead", source="pm", threads=[idea_th])])

    out = PMCycleOutput(report="r", thread_replies=[
        {"thread_id": sprint_th["id"], "text": "done — set cpu"},
        {"thread_id": idea_th["id"], "text": "promoted it"},
    ])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)

    assert substrate.load_sprint("p1-s").threads[0]["messages"][-1]["text"] == "done — set cpu"
    _summary, ideas = substrate.load_ideas("p1")
    assert ideas[0].threads[0]["messages"][-1]["text"] == "promoted it"


def test_pm_reply_ignores_unmatched_idea_thread(substrate):
    _prog(substrate)
    substrate.save_ideas("p1", "", [Idea(id="i1", text="lead", source="pm")])
    out = PMCycleOutput(report="r", thread_replies=[{"thread_id": "nope", "text": "ignored"}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    _summary, ideas = substrate.load_ideas("p1")
    assert ideas[0].threads == []
