"""F10: the dispatcher collects finished chat turns, so no call waits on a reader."""
from tests.conftest import FakeAgent

from coscience import usage_meter
from coscience.dispatcher import Dispatcher
from coscience.models import Program, ProgramStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def test_a_finished_chat_turn_is_collected_without_anyone_opening_the_thread(substrate):
    from coscience.service import Service
    # Paused, so the cycle's wiki beat stays out of this test.
    substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.PAUSED))
    svc = Service(substrate.repo_root)
    thread = svc.create_chat("p1", title="t")
    svc.post_chat_message("p1", thread["id"], "hello", launch=lambda **kw: "tok:1")
    tdir = substrate.chat_thread_dir("p1", thread["id"])
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "turn.out").write_text("")
    (tdir / "turn.exit").write_text("0")

    Dispatcher(substrate, FakeAgent(), ResourcePool({}), SchedulerPolicy()).run_one_cycle(now=0.0)

    assert substrate.load_chat_thread("p1", thread["id"]).pending is False
    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["kind"] == "chat" and call["status"] == "ok"
