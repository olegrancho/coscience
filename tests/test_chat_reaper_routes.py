from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.dispatcher import Dispatcher
from coscience.http_api import build_app
from coscience.models import Program
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service
from tests.conftest import FakeAgent


def _svc(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    return Service(substrate.repo_root)


def test_reaper_releases_idle_chat_lock_in_cycle(substrate):
    svc = _svc(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc.create_chat("p", artifacts=["doc"])          # locks doc at t=now
    # force the lock old
    a = substrate.load_artifact("p", "doc")
    a.lock["last_activity"] = 0.0
    substrate.save_artifact(a)
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool({"cpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=1801.0)
    assert substrate.load_artifact("p", "doc").lock == {}   # reaped


def test_create_chat_route_accepts_artifacts(substrate):
    _svc(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    c = TestClient(build_app(Service(substrate.repo_root)))
    r = c.post("/api/programs/p/chats", json={"title": "edit", "artifacts": ["doc"]})
    assert r.status_code == 201
    assert r.json()["artifacts"] == ["doc"]


def test_save_route_and_work_read(substrate):
    svc = _svc(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    cid = svc.create_chat("p", artifacts=["doc"])["id"]
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("live edit")
    c = TestClient(build_app(Service(substrate.repo_root)))
    assert c.get("/api/programs/p/artifacts/doc/work/c.md").json()["content"] == "live edit"
    r = c.post(f"/api/programs/p/chats/{cid}/save")
    assert r.json() == {"doc": "v1"}


def test_reaper_skips_pending_chat(substrate):
    from coscience import artifacts as _art
    svc = _svc(substrate)
    _art.create_artifact(substrate, "p", "doc", "Doc", "md")
    cid = svc.create_chat("p", artifacts=["doc"])["id"]
    # make it look idle BUT mark the chat pending (a turn in flight)
    a = substrate.load_artifact("p", "doc"); a.lock["last_activity"] = 0.0
    substrate.save_artifact(a)
    t = substrate.load_chat_thread("p", cid); t.pending = True
    substrate.save_chat_thread("p", t)
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool({"cpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=999999.0)
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == f"chat:{cid}"  # NOT reaped
