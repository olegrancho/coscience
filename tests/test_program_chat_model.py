"""H5: chat runs on its own model instead of borrowing the planner's."""

from __future__ import annotations

from coscience import usage_meter
from coscience.models import Program


def test_an_unset_chat_model_keeps_talking_to_the_planner_model():
    # Before H5 chat always ran on pm_model; an old program must not change model.
    assert Program(id="p", title="P", goals="g", pm_model="claude-opus-5").chat_model == "claude-opus-5"


def test_the_chat_model_round_trips_through_program_md(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g",
                                   pm_model="claude-opus-5", chat_model="claude-fable-5-1"))
    got = substrate.load_program("p1")
    assert got.chat_model == "claude-fable-5-1" and got.pm_model == "claude-opus-5"


def test_a_chat_turn_runs_on_the_chat_model_not_the_planner_model(substrate):
    from coscience.service import Service
    substrate.save_program(Program(id="p1", title="P", goals="g", pm_model="claude-opus-5"))
    svc = Service(substrate.repo_root)
    assert svc.set_program_chat_model("p1", "claude-sonnet-5")["chat_model"] == "claude-sonnet-5"
    assert svc.get_program("p1")["pm_model"] == "claude-opus-5"
    thread = svc.create_chat("p1", title="t")

    launched = {}

    def fake_launch(**kw):
        launched.update(kw)
        return "tok:1"

    svc.post_chat_message("p1", thread["id"], "hello", launch=fake_launch)

    assert launched["model"] == "claude-sonnet-5"
    (call,) = usage_meter.calls(substrate.repo_root, now=10.0)
    assert call["model"] == "claude-sonnet-5"


def test_clearing_the_chat_model_falls_back_to_the_planner_model(substrate):
    from coscience.service import Service
    substrate.save_program(Program(id="p1", title="P", goals="g", pm_model="claude-opus-5",
                                   chat_model="claude-sonnet-5"))
    assert Service(substrate.repo_root).set_program_chat_model("p1", "")["chat_model"] == "claude-opus-5"
