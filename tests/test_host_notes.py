"""O9: a program's own notes per server, and the reports workers file about a server.

A note is `programs/<id>/hosts/<host>.md`; `local` names this machine. Reports the PM
has not yet folded in wait in `programs/<id>/hosts/reports.json`.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeAgent

from coscience import escalation
from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.http_api import build_app
from coscience.models import (BeatOutcome, Program, ProgramStatus, ProgressState,
                              Sprint, SprintStatus)
from coscience.service import Service
from coscience.substrate import Substrate
from coscience.worker import Worker


def _program(sub, program_id="p1"):
    sub.save_program(Program(id=program_id, title="P", goals="g",
                             status=ProgramStatus.ACTIVE))


# --- the substrate stores notes and reports -----------------------------------------

def test_a_note_round_trips(substrate):
    _program(substrate)
    substrate.save_host_note("p1", "gpu1", "  torch 2.3 lives in env t23  ")
    assert substrate.load_host_note("p1", "gpu1") == "torch 2.3 lives in env t23"
    assert substrate.load_host_note("p1", "never-used") == ""


def test_saving_an_empty_note_removes_the_file(substrate):
    _program(substrate)
    substrate.save_host_note("p1", "gpu1", "something")
    substrate.save_host_note("p1", "gpu1", "   ")
    assert substrate.load_host_note("p1", "gpu1") == ""
    assert not (substrate.host_notes_dir("p1") / "gpu1.md").exists()


def test_list_host_notes_returns_every_note(substrate):
    _program(substrate)
    substrate.save_host_note("p1", "local", "this box has no GPU")
    substrate.save_host_note("p1", "gpu1", "4 x A100")
    assert substrate.list_host_notes("p1") == {"local": "this box has no GPU",
                                               "gpu1": "4 x A100"}


def test_a_host_name_that_is_a_path_is_refused(substrate):
    _program(substrate)
    for bad in ("../x", "a b", "a/b", ""):
        with pytest.raises(ValueError):
            substrate.save_host_note("p1", bad, "text")
        with pytest.raises(ValueError):
            substrate.load_host_note("p1", bad)
        with pytest.raises(ValueError):
            substrate.clear_host_reports("p1", bad)


def test_clearing_one_servers_reports_leaves_the_others(substrate):
    _program(substrate)
    substrate.add_host_report("p1", sprint_id="s1", host="a", text="CUDA 11 only",
                              source="finished", now=100.0)
    substrate.add_host_report("p1", sprint_id="s2", host="a", text="disk full",
                              source="escalation", now=200.0)
    kept = substrate.add_host_report("p1", sprint_id="s3", host="local", text="slow disk",
                                     source="finished", now=300.0)
    assert kept["id"] == "s3:finished:300"
    assert len(substrate.load_host_reports("p1")) == 3
    assert substrate.clear_host_reports("p1", "a") == 2
    left = substrate.load_host_reports("p1")
    assert left == [{"id": "s3:finished:300", "sprint_id": "s3", "host": "local",
                     "text": "slow disk", "source": "finished", "at": 300.0}]


def test_an_empty_report_is_not_filed(substrate):
    _program(substrate)
    assert substrate.add_host_report("p1", sprint_id="s1", host="a", text="   ",
                                     source="finished", now=100.0) == {}
    assert substrate.load_host_reports("p1") == []


# --- the worker agent's instructions carry the note ---------------------------------

def _sprint():
    return Sprint(id="sp1", status=SprintStatus.APPROVED, goals="report the min gap",
                  plan=["scan"], title="Baseline scan", summary="scan primes")


def test_a_remote_agent_reads_the_machine_notes_then_the_programs_own(tmp_path):
    ctx = ExecutionContext(host_name="a", host_ssh="a", host_run_dir="/scratch/sp1",
                           host_notes="machine rule", program_host_notes="use conda env torch2")
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    assert "machine rule" in text and "use conda env torch2" in text
    assert text.index("machine rule") < text.index("use conda env torch2")
    assert "This program's notes on this host" in text


def test_a_local_agent_reads_the_programs_notes_on_this_machine(tmp_path):
    ctx = ExecutionContext(program_host_notes="the shared scratch is at /data/scratch")
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    assert "## This program's notes on this machine" in text
    assert "the shared scratch is at /data/scratch" in text


def test_without_a_program_note_neither_section_appears(tmp_path):
    ctx = ExecutionContext(host_name="a", host_ssh="a", host_run_dir="/scratch/sp1",
                           host_notes="machine rule")
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    assert "This program's notes on this host" not in text
    assert "This program's notes on this machine" not in build_instructions(
        _sprint(), ExecutionContext(), tmp_path / "scratchpad.md")


def test_the_instructions_ask_for_host_notes_in_both_signals(tmp_path):
    text = build_instructions(_sprint(), None, tmp_path / "scratchpad.md")
    assert text.count('"host_notes"') == 2      # finished.json and escalate.json


# --- the worker fills the context and files reports ---------------------------------

class _Slots:
    """A lease on one named host, for a worker built without a dispatcher."""

    def __init__(self, name="local", ssh=""):
        self._host = {"name": name, "ssh": ssh, "run_root": "/scratch" if ssh else "",
                      "facts": "", "notes": ""}

    def release(self, sprint_id): pass
    def acquire(self, sprint_id): return True
    def gpus(self, sprint_id): return [], None
    def host(self, sprint_id): return dict(self._host)
    def ssh_for(self, host_name): return self._host["ssh"]
    def host_quiet(self, host_name): return False


def _queued(sub, sid="s1", program="p1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"],
                           program=program))


def test_the_context_carries_the_note_for_the_host_the_lease_is_on(substrate):
    _program(substrate)
    _queued(substrate)
    substrate.save_host_note("p1", "a", "use conda env torch2")
    substrate.save_host_note("p1", "local", "no GPU here")
    w = Worker(substrate, FakeAgent(), slots=_Slots("a", ssh="a"))
    assert w._build_context(substrate.load_sprint("s1")).program_host_notes == \
        "use conda env torch2"
    w_local = Worker(substrate, FakeAgent())
    assert w_local._build_context(substrate.load_sprint("s1")).program_host_notes == \
        "no GPU here"


def test_a_finished_sprint_files_what_it_learned_about_its_host(substrate):
    _program(substrate)
    _queued(substrate)

    class Learned(FakeAgent):
        def start(self, sprint, ctx, sprint_dir, repo_root=None):
            tok = super().start(sprint, ctx, sprint_dir, repo_root)
            (Path(sprint_dir) / "finished.json").write_text(
                json.dumps({"summary": "done", "host_notes": "CUDA 11 only"}))
            return tok

    w = Worker(substrate, Learned(), slots=_Slots("a", ssh="a"))
    w.run_one_beat()
    assert w.run_one_beat() == BeatOutcome.COMPLETED
    assert substrate.load_sprint("s1").status == SprintStatus.DONE
    assert "done" in substrate.load_result("s1-result").summary
    reports = substrate.load_host_reports("p1")
    assert len(reports) == 1
    assert reports[0]["sprint_id"] == "s1" and reports[0]["host"] == "a"
    assert reports[0]["text"] == "CUDA 11 only" and reports[0]["source"] == "finished"


def test_a_finished_json_with_no_usable_host_notes_files_nothing(substrate):
    _program(substrate)

    for sid, payload in (("s1", {"summary": "done", "host_notes": 5}),
                         ("s2", {"summary": "done"})):
        _queued(substrate, sid)

        class Agent(FakeAgent):
            def start(self, sprint, ctx, sprint_dir, repo_root=None):
                tok = super().start(sprint, ctx, sprint_dir, repo_root)
                (Path(sprint_dir) / "finished.json").write_text(json.dumps(payload))
                return tok

        w = Worker(substrate, Agent())
        w.run_one_beat()
        w.run_one_beat()
        assert substrate.load_sprint(sid).status == SprintStatus.DONE
    assert substrate.load_host_reports("p1") == []


def test_an_escalation_that_learned_something_files_a_report(substrate):
    _program(substrate)
    sprint = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"],
                    program="p1")
    substrate.save_sprint(sprint)
    progress = ProgressState(sprint_id="s1", host="a")
    substrate.save_progress(progress)
    escalation.raise_escalation(substrate, sprint, progress,
                                {"what": "no disk", "tried": "rm -rf", "needs": "space",
                                 "host_notes": "disk full at /scratch", "by": "agent",
                                 "host": "a"}, now=100.0)
    reports = substrate.load_host_reports("p1")
    assert len(reports) == 1
    assert reports[0]["host"] == "a" and reports[0]["source"] == "escalation"
    assert reports[0]["text"] == "disk full at /scratch"


def test_an_escalation_without_host_notes_files_nothing(substrate):
    _program(substrate)
    sprint = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"],
                    program="p1")
    substrate.save_sprint(sprint)
    progress = ProgressState(sprint_id="s1")
    substrate.save_progress(progress)
    escalation.raise_escalation(substrate, sprint, progress,
                                {"what": "no disk", "host_notes": "", "by": "agent"},
                                now=100.0)
    assert substrate.load_host_reports("p1") == []


# --- a human reads and edits the notes over HTTP ------------------------------------

@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g",
                                       status=ProgramStatus.ACTIVE))
    c = TestClient(build_app(svc))
    c.svc = svc
    return c


def test_get_returns_the_notes_and_the_pending_reports(client):
    sub = client.svc.substrate
    sub.save_host_note("p1", "a", "4 x A100")
    sub.add_host_report("p1", sprint_id="s1", host="a", text="CUDA 11 only",
                        source="finished", now=100.0)
    r = client.get("/api/programs/p1/host-notes")
    assert r.status_code == 200
    body = r.json()
    assert body["notes"] == {"a": "4 x A100"}
    assert [(x["host"], x["text"], x["source"]) for x in body["reports"]] == \
        [("a", "CUDA 11 only", "finished")]


def test_saving_a_note_clears_that_servers_reports_and_commits(client, monkeypatch):
    sub = client.svc.substrate
    sub.add_host_report("p1", sprint_id="s1", host="a", text="CUDA 11 only",
                        source="finished", now=100.0)
    sub.add_host_report("p1", sprint_id="s2", host="local", text="slow disk",
                        source="finished", now=200.0)
    messages = []
    monkeypatch.setattr(sub, "commit", lambda msg: messages.append(msg))
    r = client.put("/api/programs/p1/host-notes/a", json={"text": "CUDA 11 only; torch 2.3"})
    assert r.status_code == 200
    assert r.json()["notes"]["a"] == "CUDA 11 only; torch 2.3"
    assert [x["host"] for x in r.json()["reports"]] == ["local"]
    assert messages == ["program p1: notes on a updated"]


def test_saving_an_empty_note_over_http_deletes_it(client):
    client.svc.substrate.save_host_note("p1", "a", "stale")
    r = client.put("/api/programs/p1/host-notes/a", json={"text": ""})
    assert r.status_code == 200
    assert r.json()["notes"] == {}


def test_a_bad_server_name_is_422_and_an_unknown_program_is_404(client):
    assert client.put("/api/programs/p1/host-notes/a b", json={"text": "x"}).status_code == 422
    assert client.put("/api/programs/nope/host-notes/a", json={"text": "x"}).status_code == 404
    assert client.get("/api/programs/nope/host-notes").status_code == 404


def _pool(client, text="hosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n"
                       "  b:\n    ssh: b\n    programs: [p2]\n    capacity: {cpu: 8}\n"):
    cos = client.svc.substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_get_names_every_server_and_whether_the_program_may_use_it(client):
    """O23: all the notes card needs to know about compute, so it can stop polling the
    whole ledger — which parses every sprint on the box — every ten seconds."""
    _pool(client)
    hosts = client.get("/api/programs/p1/host-notes").json()["hosts"]
    assert {h["name"]: h["allowed"] for h in hosts} == {"local": True, "a": True, "b": False}
    assert set(hosts[0]) == {"name", "label", "allowed"}


def test_a_note_cannot_be_started_on_a_server_the_program_may_not_use(client):
    """O22: a mistyped server in a URL used to create a note the planner could then
    never touch."""
    _pool(client)
    r = client.put("/api/programs/p1/host-notes/typo", json={"text": "x"})
    assert r.status_code == 422 and "may not use typo" in r.json()["detail"]
    assert client.put("/api/programs/p1/host-notes/b", json={"text": "x"}).status_code == 422
    assert client.svc.substrate.list_host_notes("p1") == {}


def test_a_note_left_on_a_withdrawn_server_can_still_be_edited_and_cleared(client):
    _pool(client)
    sub = client.svc.substrate
    sub.save_host_note("p1", "b", "from when p1 could run here")
    sub.add_host_report("p1", sprint_id="s1", host="b", text="old", source="finished", now=1.0)
    r = client.put("/api/programs/p1/host-notes/b", json={"text": ""})
    assert r.status_code == 200
    assert r.json()["notes"] == {} and r.json()["reports"] == []


def test_saving_over_a_note_that_changed_since_it_was_opened_is_refused(client):
    """O22: two editors (or an editor and the planner) used to overwrite each other."""
    _pool(client)
    client.svc.substrate.save_host_note("p1", "a", "what the other person wrote")
    r = client.put("/api/programs/p1/host-notes/a",
                   json={"text": "mine", "base": "what I opened"})
    assert r.status_code == 409
    assert r.json()["detail"]["current"] == "what the other person wrote"
    assert client.svc.substrate.load_host_note("p1", "a") == "what the other person wrote"


def test_saving_over_the_note_as_opened_goes_through(client):
    _pool(client)
    client.svc.substrate.save_host_note("p1", "a", "as opened")
    r = client.put("/api/programs/p1/host-notes/a", json={"text": "mine", "base": "as opened"})
    assert r.status_code == 200 and r.json()["notes"]["a"] == "mine"
    # A server with no note yet has an empty base.
    r = client.put("/api/programs/p1/host-notes/local", json={"text": "first", "base": ""})
    assert r.status_code == 200 and r.json()["notes"]["local"] == "first"


# --- a note is an extra: it never breaks the work it hangs off (review round 1) ------

def test_an_escalation_is_still_raised_when_its_report_cannot_be_filed(substrate):
    # The pool accepts host names the notes layer refuses (nothing validates a
    # hand-edited `hosts:` key), and a full disk fails the same way. This is the red
    # button: it must be pulled whatever happens to the note beside it.
    _program(substrate)
    sprint = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"],
                    program="p1")
    substrate.save_sprint(sprint)
    progress = ProgressState(sprint_id="s1", host="big box")      # a name with a space
    substrate.save_progress(progress)

    escalation.raise_escalation(substrate, sprint, progress,
                                {"what": "no disk", "host_notes": "disk full at /scratch",
                                 "by": "agent", "host": "big box"}, now=100.0)

    assert substrate.load_sprint("s1").status == SprintStatus.ESCALATED
    assert substrate.load_progress("s1").escalation["what"] == "no disk"
    assert substrate.load_host_reports("p1") == []                # the note is what gave way


def test_a_finished_sprint_is_still_done_when_its_report_cannot_be_filed(substrate, monkeypatch):
    # The disk that filled on 2026-09-19 is the case: writing reports.json raises
    # mid-completion. Without a guard the sprint never reaches DONE, retries the same
    # failure every beat, and a finished sprint ends up recorded as failed.
    _program(substrate)
    _queued(substrate)

    class Learned(FakeAgent):
        def start(self, sprint, ctx, sprint_dir, repo_root=None):
            tok = super().start(sprint, ctx, sprint_dir, repo_root)
            (Path(sprint_dir) / "finished.json").write_text(
                json.dumps({"summary": "done", "host_notes": "CUDA 11 only"}))
            return tok

    def boom(*a, **kw):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(substrate, "add_host_report", boom)

    w = Worker(substrate, Learned(), slots=_Slots("a", ssh="a"))
    w.run_one_beat()
    assert w.run_one_beat() == BeatOutcome.COMPLETED
    assert substrate.load_sprint("s1").status == SprintStatus.DONE
    assert "done" in substrate.load_result("s1-result").summary


def test_a_human_save_clears_only_the_reports_the_page_showed(substrate):
    _program(substrate)
    svc = Service(substrate.repo_root)
    shown = substrate.add_host_report("p1", sprint_id="p1-s1", host="a", text="shown",
                                      source="finished", now=100.0)
    later = substrate.add_host_report("p1", sprint_id="p1-s2", host="a", text="filed after the page loaded",
                                      source="finished", now=200.0)

    svc.set_host_note("p1", "a", "folded what I read", report_ids=[shown["id"]])

    left = substrate.load_host_reports("p1")
    assert [r["id"] for r in left] == [later["id"]]
    assert substrate.load_host_note("p1", "a") == "folded what I read"


def test_an_older_dashboard_that_names_no_reports_still_clears_the_server(substrate):
    _program(substrate)
    svc = Service(substrate.repo_root)
    substrate.add_host_report("p1", sprint_id="p1-s1", host="a", text="x",
                              source="finished", now=100.0)
    svc.set_host_note("p1", "a", "note")
    assert substrate.load_host_reports("p1") == []
