"""Standing house rules for a program's PM: stored, rendered into every PM prompt,
and a change to them wakes the PM."""
from fastapi.testclient import TestClient

from coscience import chat_agent
from coscience.http_api import build_app
from coscience.models import Program, ProgramStatus
from coscience.pm_agent import context_fingerprint, context_signals, gather_context, _triggers
from coscience.pm_claude import render_chat_prompt, render_prompt
from coscience.pm_reasoner import PMContext
from coscience.service import Service


def _c(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return TestClient(build_app(svc)), svc


# --- storage ---

def test_instructions_roundtrip(substrate):
    substrate.save_instructions("p1", "  Write in British English.\n")
    assert substrate.load_instructions("p1") == "Write in British English."


def test_no_instructions_reads_as_empty(substrate):
    assert substrate.load_instructions("p1") == ""


def test_clearing_instructions_removes_the_file(substrate):
    substrate.save_instructions("p1", "some policy")
    substrate.save_instructions("p1", "   ")
    assert substrate.load_instructions("p1") == ""
    assert not (substrate.program_dir("p1") / "instructions.md").is_file()


# --- the prompts ---

def _ctx(instructions=""):
    return PMContext(program_id="p1", goals="cure it", cycle=0, instructions=instructions)


def test_every_pm_prompt_carries_the_instructions():
    ctx = _ctx("Never propose animal work.")
    for prompt in (render_prompt(ctx),
                   render_chat_prompt(ctx, [], "hi?"),
                   chat_agent.render_preamble(ctx, "read")):
        assert "GENERAL INSTRUCTIONS" in prompt
        assert "Never propose animal work." in prompt


def test_no_instructions_renders_no_block():
    ctx = _ctx("")
    for prompt in (render_prompt(ctx),
                   render_chat_prompt(ctx, [], "hi?"),
                   chat_agent.render_preamble(ctx, "read")):
        assert "GENERAL INSTRUCTIONS" not in prompt


def test_instructions_are_not_presented_as_something_to_answer():
    # They must not read like guidance: a thread_replies entry against a house rule
    # would be noise every cycle.
    prompt = render_prompt(_ctx("Cite sources."))
    block = prompt.split("GENERAL INSTRUCTIONS", 1)[1].split("Cite sources.")[0]
    assert "do not reply to them" in block


# --- waking the PM ---

def test_editing_instructions_triggers_a_cycle(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    before = gather_context(substrate, "p1")
    substrate.save_instructions("p1", "Prefer cheap models.")
    after = gather_context(substrate, "p1")
    assert context_fingerprint(before) != context_fingerprint(after)
    assert _triggers(context_signals(before), context_signals(after), forced=False) \
        == ["instructions edited"]


def test_a_program_without_instructions_is_not_woken_by_this_feature(substrate):
    # The fingerprint is persisted per program, so a new payload key would look like a
    # change to every existing program the first time this code runs. It must not.
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    assert "instructions" not in context_signals(gather_context(substrate, "p1"))


# --- the API ---

def test_program_payload_carries_instructions_and_the_route_sets_them(tmp_path):
    c, _svc = _c(tmp_path)
    assert c.get("/api/programs/p1").json()["instructions"] == ""
    r = c.post("/api/programs/p1/instructions", json={"text": "Be terse."})
    assert r.status_code == 200
    assert r.json()["instructions"] == "Be terse."
    assert c.get("/api/programs/p1").json()["instructions"] == "Be terse."


def test_setting_instructions_on_a_missing_program_is_404(tmp_path):
    c, _svc = _c(tmp_path)
    assert c.post("/api/programs/nope/instructions", json={"text": "x"}).status_code == 404
