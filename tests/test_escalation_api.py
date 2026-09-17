"""O8 Task 4: a human sees and answers escalations, via the service and HTTP layers."""
import pytest
from fastapi.testclient import TestClient

from coscience import escalation
from coscience.http_api import build_app
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service

RECORD = {"what": "gpu1 refuses SSH since 13:10; job 4121 state unknown",
          "tried": "three retries over 20 min", "may_have_broken_something": False,
          "needs": "a working host, or confirmation the job is still running"}

POOL = ResourcePool.from_dict({"cpu": 4, "hosts": {
    "gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
    "gpu2": {"ssh": "gpu2", "capacity": {"cpu": 8}, "programs": ["other"]},
    "gpu3": {"ssh": "gpu3", "capacity": {"cpu": 8}},
}})


def _executing(svc, sid="s1", program="p1"):
    sp = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["a"], program=program)
    svc.substrate.save_sprint(sp)
    svc.substrate.save_progress(ProgressState(sprint_id=sid))
    return sp


def _escalated(svc, sid="s1", level="pm", host="gpu1", program="p1"):
    sp = _executing(svc, sid, program)
    progress = svc.substrate.load_progress(sid)
    progress.host = host
    escalation.raise_escalation(svc.substrate, sp, progress, {**RECORD, "by": "agent", "host": host}, now=1.0)
    if level == "human":
        progress = svc.substrate.load_progress(sid)
        progress.escalation["level"] = "human"
        svc.substrate.save_progress(progress)
    return svc.substrate.load_sprint(sid)


# --- 1: get_sprint / list_sprints carry the escalation ------------------------------

def test_get_sprint_carries_escalation_with_hosts_allowed(tmp_path, every_host_placeable):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc)
    d = svc.get_sprint("s1")
    esc = d["escalation"]
    assert esc["level"] == "pm"
    assert esc["what"] == RECORD["what"]
    assert esc["host"] == "gpu1"
    assert esc["thread_id"]
    # pinned to gpu1 (excluded); gpu2 doesn't allow program p1; gpu3 and local are left
    assert set(esc["hosts_allowed"]) == {"gpu3", "local"}
    assert esc["stop_requested"] is False


def test_get_sprint_hosts_allowed_omits_a_drained_and_a_quiet_host(tmp_path, every_host_placeable):
    from coscience import host_health
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
        "gpu2": {"ssh": "gpu2", "capacity": {"cpu": 8}, "drain": True},
        "gpu3": {"ssh": "gpu3", "capacity": {"cpu": 8}},
    }})
    svc = Service(tmp_path, pool=pool)
    host_health._save(tmp_path, {
        "gpu3": {"checked_at": 1.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "down"}})
    _escalated(svc, host="gpu1")
    esc = svc.get_sprint("s1")["escalation"]
    assert set(esc["hosts_allowed"]) == {"local"}


def test_get_sprint_escalation_shows_stop_requested(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc)
    escalation.answer(svc.substrate, "s1", "stop", by="oleg")
    esc = svc.get_sprint("s1")["escalation"]
    assert esc["stop_requested"] is True


def test_get_sprint_escalation_is_none_when_not_escalated(tmp_path):
    svc = Service(tmp_path)
    _executing(svc)
    assert svc.get_sprint("s1")["escalation"] is None


def test_list_sprints_rows_carry_escalation_level(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc, level="human")
    svc.submit_sprint(id="s2", goals="g", plan=["a"])
    rows = {r["id"]: r for r in svc.list_sprints()}
    assert rows["s1"]["escalation_level"] == "human"
    assert rows["s2"]["escalation_level"] == ""


# --- 2: answer_escalation ------------------------------------------------------------

def test_answer_escalation_resume_returns_the_executing_sprint(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc)
    d = svc.answer_escalation("s1", "resume", instructions="go", by="oleg")
    assert d["status"] == "executing"


def test_answer_escalation_bad_action_raises_value_error(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc)
    with pytest.raises(ValueError):
        svc.answer_escalation("s1", "nonsense", by="oleg")


def test_answer_escalation_missing_sprint_raises_not_found(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    with pytest.raises(NotFoundError):
        svc.answer_escalation("nope", "resume", by="oleg")


def test_answer_escalation_stale_thread_id_raises_value_error(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc)
    with pytest.raises(ValueError):
        svc.answer_escalation("s1", "resume", by="oleg", thread_id="not-the-real-one")


# --- resume_sprint clears leftover escalation state (Task 2 review) -----------------

def test_resume_sprint_clears_leftover_escalation_state(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    sp = Sprint(id="s1", status=SprintStatus.FAILED, goals="g", plan=["a"])
    svc.substrate.save_sprint(sp)
    svc.substrate.save_progress(ProgressState(
        sprint_id="s1", pm_answered=True, escalation={"level": "pm", "what": "x"},
        resume_note="use gpu3", reallocate_to="gpu3", stop_requested=True))
    svc.resume_sprint("s1", by="oleg")
    progress = svc.substrate.load_progress("s1")
    assert progress.pm_answered is False
    assert progress.escalation == {}
    assert progress.resume_note == ""
    assert progress.reallocate_to == ""
    assert progress.stop_requested is False


# --- 3: attention ---------------------------------------------------------------------

def test_attention_lists_only_human_level_escalations(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    _escalated(svc, sid="s1", level="pm")
    _escalated(svc, sid="s2", level="human")
    rows = svc.attention()["escalated_to_human"]
    assert [r["sprint_id"] for r in rows] == ["s2"]
    assert rows[0]["what"] == RECORD["what"]


# --- 4: HTTP ----------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path, pool=POOL)
    client = TestClient(build_app(svc))
    client.svc = svc  # expose for seeding
    return client


def test_post_escalation_stop_sets_stop_requested(client):
    _escalated(client.svc)
    r = client.post("/api/sprints/s1/escalation", json={"action": "stop"})
    assert r.status_code == 200
    assert client.svc.substrate.load_progress("s1").stop_requested is True


def test_post_escalation_with_a_stale_thread_id_is_422(client):
    _escalated(client.svc)
    r = client.post("/api/sprints/s1/escalation",
                    json={"action": "resume", "thread_id": "not-the-real-one"})
    assert r.status_code == 422


def test_post_escalation_with_the_current_thread_id_succeeds(client):
    sp = _escalated(client.svc)
    thread_id = sp.threads[-1]["id"]
    r = client.post("/api/sprints/s1/escalation",
                    json={"action": "resume", "instructions": "go", "thread_id": thread_id})
    assert r.status_code == 200


def test_post_escalation_to_human_from_a_human_is_422(client):
    _escalated(client.svc)
    r = client.post("/api/sprints/s1/escalation", json={"action": "to_human"})
    assert r.status_code == 422


def test_post_escalation_unknown_sprint_is_404(client):
    r = client.post("/api/sprints/nope/escalation", json={"action": "resume"})
    assert r.status_code == 404


def test_get_attention_returns_the_list(client):
    _escalated(client.svc, level="human")
    r = client.get("/api/attention")
    assert r.status_code == 200
    body = r.json()
    assert body["escalated_to_human"][0]["sprint_id"] == "s1"
