"""Lifecycle status timeline: set_status dedup, persistence/seed, and that
every transition site (service, worker, dispatcher) records into status_history."""
import time

from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus, set_status
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service
from coscience.substrate import Substrate
from coscience.worker import Worker


# --- set_status helper -------------------------------------------------------

def test_set_status_appends_on_change():
    s = Sprint(id="s", status=SprintStatus.PROPOSED, goals="g")
    set_status(s, SprintStatus.APPROVED, by="oleg", action="approve")
    assert s.status == SprintStatus.APPROVED
    assert s.status_history == [
        {"status": "approved", "at": s.status_history[0]["at"], "by": "oleg", "action": "approve"}]


def test_set_status_dedups_same_status():
    s = Sprint(id="s", status=SprintStatus.QUEUED, goals="g")
    set_status(s, SprintStatus.EXECUTING)      # dispatcher grant
    set_status(s, SprintStatus.EXECUTING)      # worker start, same cycle
    assert [h["status"] for h in s.status_history] == ["executing"]


# --- substrate persistence + seed -------------------------------------------

def test_first_save_seeds_initial_entry(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"]))
    hist = sub.load_sprint("s1").status_history
    assert [h["status"] for h in hist] == ["proposed"]
    assert hist[0]["at"] == sub.load_sprint("s1").created_at


def test_status_history_roundtrips(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"])
    sub.save_sprint(s)                          # seeds proposed
    set_status(s, SprintStatus.APPROVED, by="oleg", action="approve")
    sub.save_sprint(s)
    hist = sub.load_sprint("s1").status_history
    assert [h["status"] for h in hist] == ["proposed", "approved"]
    assert hist[1]["by"] == "oleg" and hist[1]["action"] == "approve"


def test_legacy_sprint_not_reseeded(tmp_path):
    # A sprint already on disk (no status_history) must not get an initial seed on
    # a later save — its legacy `decisions` trail stands in for early events.
    sub = Substrate(tmp_path)
    d = sub.sprint_dir("s1"); d.mkdir(parents=True, exist_ok=True)
    (d / "sprint.md").write_text("---\nstatus: approved\ngoals: g\ncreated_at: 100.0\n---\n# old\n")
    s = sub.load_sprint("s1")
    assert s.status_history == []
    sub.save_sprint(s)                          # re-save; created_at already set
    assert sub.load_sprint("s1").status_history == []


# --- transition sites record -------------------------------------------------

def _queued(sub, sid="s1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1"))


def test_worker_records_executing_then_done(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    w = Worker(sub, FakeAgent())
    w.run_one_beat()                            # claim -> executing
    w.run_one_beat()                            # agent done -> done
    hist = sub.load_sprint("s1").status_history
    assert [h["status"] for h in hist] == ["queued", "executing", "done"]   # queued seeded at birth
    assert all(h["action"] == "" for h in hist)   # system transitions


def test_dispatcher_grant_records_executing(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    disp = Dispatcher(sub, FakeAgent(linger=10**6), ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=0.0)
    assert "executing" in [h["status"] for h in sub.load_sprint("s1").status_history]


# --- service payload ---------------------------------------------------------

def test_get_sprint_payload_has_timeline_fields(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g",
                                     plan=["a"], program="p1"))
    svc.approve_sprint("s1", by="oleg")
    payload = svc.get_sprint("s1")
    assert payload["created_at"] is not None
    statuses = [h["status"] for h in payload["status_history"]]
    assert statuses == ["proposed", "approved"]
    assert payload["status_history"][-1]["by"] == "oleg"
