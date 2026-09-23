"""O9: the PM reads a program's per-server notes and the reports waiting to be
folded into them, and its cycle output rewrites a note and clears that server's
reports.

The notes belong to the program, not the machine: the PM may only write one for a
server this program is actually allowed to use.
"""
import json

from coscience.models import Program
from coscience.pm_agent import (_context_payload, gather_context, pm_beat,
                                read_staging, write_staging)
from coscience.pm_claude import parse_response, render_prompt
from coscience.pm_reasoner import FakeReasoner, PMContext, PMCycleOutput


def _program(substrate, program_id="p1"):
    substrate.save_program(Program(id=program_id, title="P", goals="g"))


def _hosts_yaml(substrate, text="hosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n"
                               "  b:\n    ssh: b\n    programs: [p2]\n    capacity: {cpu: 8}\n"):
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def _report(substrate, program_id="p1", *, sprint_id="p1-s1", host="a",
            text="conda env t23 has torch 2.3", source="finished", now=100.0):
    return substrate.add_host_report(program_id, sprint_id=sprint_id, host=host,
                                     text=text, source=source, now=now)


# --- context: the PM sees its notes and its pending reports -------------------------

def test_gather_context_carries_the_programs_notes_and_pending_reports(substrate):
    _program(substrate)
    _program(substrate, "p2")
    _hosts_yaml(substrate)
    substrate.save_host_note("p1", "a", "torch 2.3 in env t23")
    substrate.save_host_note("p2", "a", "another program's business")
    rep = _report(substrate)

    ctx = gather_context(substrate, "p1")

    assert ctx.host_notes == {"a": "torch 2.3 in env t23"}
    assert ctx.host_reports == [rep]


def test_the_pm_is_not_shown_what_it_could_never_write_back(substrate):
    """O22. A server this program may no longer use: its note and reports stay on the
    program page for a human, but the planner is not asked to fold in what the apply
    would refuse — it used to try, be refused, and say so, every cycle."""
    _program(substrate)
    _hosts_yaml(substrate)                   # p1 may use a, not b
    substrate.save_host_note("p1", "b", "from when p1 could run here")
    _report(substrate, host="b")
    _report(substrate, host="gone")          # a server no longer in the pool at all
    kept = _report(substrate, host="a")

    ctx = gather_context(substrate, "p1")

    assert ctx.host_notes == {}
    assert ctx.host_reports == [kept]
    assert len(substrate.load_host_reports("p1")) == 3   # nothing was cleared, only withheld


def test_a_program_with_no_notes_sees_none(substrate):
    _program(substrate)
    ctx = gather_context(substrate, "p1")
    assert ctx.host_notes == {}
    assert ctx.host_reports == []


# --- the fingerprint: a new report wakes the PM, an edited note does not ------------

def test_a_program_with_no_reports_has_no_host_reports_fingerprint_key():
    assert "host_reports" not in _context_payload(PMContext(program_id="p1", goals="g", cycle=0))


def test_a_new_report_changes_the_fingerprint(substrate):
    _program(substrate)
    _hosts_yaml(substrate)
    before = _context_payload(gather_context(substrate, "p1"))
    _report(substrate)
    after = _context_payload(gather_context(substrate, "p1"))

    assert before != after
    assert after["host_reports"] == ["p1-s1:finished:100"]


def test_rewriting_a_note_does_not_change_the_fingerprint(substrate):
    # Otherwise the PM's own fold-in would wake it again next beat, forever.
    _program(substrate)
    _hosts_yaml(substrate)
    _report(substrate)
    before = _context_payload(gather_context(substrate, "p1"))
    substrate.save_host_note("p1", "a", "torch 2.3 in env t23")
    after = _context_payload(gather_context(substrate, "p1"))

    assert before == after


# --- the prompt ---------------------------------------------------------------------

def test_the_prompt_shows_each_note_and_each_pending_report():
    ctx = PMContext(program_id="p1", goals="g", cycle=0,
                    host_notes={"a": "torch 2.3 in env t23"},
                    host_reports=[{"id": "p1-s1:finished:100", "sprint_id": "p1-s1",
                                   "host": "a", "text": "the scratch disk fills up",
                                   "source": "finished", "at": 100.0}])
    p = render_prompt(ctx)

    assert "HOST NOTES" in p
    assert "### a\ntorch 2.3 in env t23" in p
    assert "HOST REPORTS" in p
    assert "- p1-s1 on a (finished): the scratch disk fills up" in p
    assert '"host_notes"' in p


def test_the_prompt_says_nothing_about_hosts_when_there_are_no_notes_or_reports():
    p = render_prompt(PMContext(program_id="p1", goals="g", cycle=0))
    assert "HOST NOTES" not in p
    assert "HOST REPORTS" not in p


def test_host_notes_are_parsed_from_the_models_json():
    text = json.dumps({"report": "r", "host_notes": [
        {"host": "a", "text": "torch 2.3 in env t23"},
        {"host": "local"},                 # no text: mark the reports read
        {"host": ""},                      # no host -> dropped
        "not-a-dict",                      # non-dict -> dropped
    ]})
    out = parse_response(text)
    assert out.host_notes == [{"host": "a", "text": "torch 2.3 in env t23"},
                              {"host": "local"}]


# --- the staged cycle round trip ----------------------------------------------------

def test_staged_host_notes_survive_a_restart(substrate):
    _program(substrate)
    write_staging(substrate, "p1", 3,
                  PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "t"}]))
    assert read_staging(substrate, "p1").output.host_notes == [{"host": "a", "text": "t"}]


# --- apply --------------------------------------------------------------------------

def test_a_note_with_text_is_written_and_that_servers_reports_are_cleared(substrate):
    _program(substrate)
    _hosts_yaml(substrate)
    _report(substrate)
    _report(substrate, sprint_id="p1-s2", host="local", text="no GPU here", now=101.0)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "torch 2.3 in env t23"}])]),
        force=True)

    assert substrate.load_host_note("p1", "a") == "torch 2.3 in env t23"
    assert [r["host"] for r in substrate.load_host_reports("p1")] == ["local"]
    assert summary["host_notes_updated"] == ["a"]
    assert summary["host_note_skipped"] == []
    assert "Host notes updated: a" in substrate.load_report("p1")


def test_an_entry_without_text_only_marks_the_reports_read(substrate):
    _program(substrate)
    _hosts_yaml(substrate)
    substrate.save_host_note("p1", "a", "the note as it stands")
    _report(substrate)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "a"}])]), force=True)

    assert substrate.load_host_note("p1", "a") == "the note as it stands"
    assert substrate.load_host_reports("p1") == []
    assert summary["host_notes_updated"] == []      # nothing was written
    assert summary["host_note_skipped"] == []


def test_an_empty_text_deletes_the_note(substrate):
    _program(substrate)
    _hosts_yaml(substrate)
    substrate.save_host_note("p1", "local", "stale and wrong")

    pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "local", "text": ""}])]), force=True)

    assert substrate.load_host_note("p1", "local") == ""


def test_a_server_this_program_may_not_use_is_skipped_and_reported(substrate):
    _program(substrate)
    _hosts_yaml(substrate)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "b", "text": "not mine to write"}])]),
        force=True)

    assert substrate.load_host_note("p1", "b") == ""
    assert summary["host_notes_updated"] == []
    assert summary["host_note_skipped"] == [
        {"id": "b", "why": "this program may not use b"}]
    assert "Host note FAILED: `b`" in substrate.load_report("p1")
    assert "this program may not use b" in substrate.load_report("p1")


def test_a_server_that_is_not_in_the_pool_at_all_is_skipped(substrate):
    _program(substrate)
    _hosts_yaml(substrate)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "ghost", "text": "x"}])]), force=True)

    assert summary["host_note_skipped"] == [{"id": "ghost", "why": "this program may not use ghost"}]


def test_a_host_name_that_is_a_path_is_skipped_not_raised(substrate):
    _program(substrate)
    _hosts_yaml(substrate)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "../p2/hosts/a", "text": "x"}])]),
        force=True)

    assert summary["host_note_skipped"] == [
        {"id": "../p2/hosts/a", "why": "invalid server name"}]


def test_a_malformed_entry_is_skipped_silently(substrate):
    _program(substrate)
    _hosts_yaml(substrate)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=["not-a-dict", {}, {"host": 5},
                                              {"text": "no host"}])]), force=True)

    assert summary["host_notes_updated"] == []
    assert summary["host_note_skipped"] == []       # malformed, not refused
    assert "Host note" not in substrate.load_report("p1")


# --- review round 1: four ways this quietly lost knowledge ---------------------------

def test_a_null_text_marks_the_reports_read_and_leaves_the_note_alone(substrate):
    # The schema shows the model a "text" key, so `{"host": "a", "text": null}` is how
    # it most often writes "read, no change". Coercing that wrote the note "None" over
    # real knowledge AND dropped the reports that held it.
    _program(substrate)
    _hosts_yaml(substrate)
    substrate.save_host_note("p1", "a", "torch 2.3 lives in env t23")
    _report(substrate)

    parsed = parse_response(json.dumps({"report": "r", "host_notes": [
        {"host": "a", "text": None}, {"host": "local", "text": 5}]}))
    assert parsed.host_notes == [{"host": "a"}, {"host": "local"}]

    pm_beat(substrate, "p1", FakeReasoner([parsed]), force=True)
    assert substrate.load_host_note("p1", "a") == "torch 2.3 lives in env t23"
    assert substrate.load_host_reports("p1") == []


def test_a_report_filed_while_the_planner_was_thinking_is_not_cleared_unread(substrate):
    # A cycle is one long Claude call. A sprint that finishes inside that window files
    # a report the planner was never shown; clearing the whole server loses it.
    _program(substrate)
    _hosts_yaml(substrate)
    seen = _report(substrate, sprint_id="p1-s1", text="seen by the planner", now=100.0)

    class Late(FakeReasoner):
        def run(self, context):
            _report(substrate, sprint_id="p1-s9", text="filed mid-cycle", now=200.0)
            return super().run(context)

    pm_beat(substrate, "p1", Late([
        PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "folded"}])]), force=True)

    left = substrate.load_host_reports("p1")
    assert [r["text"] for r in left] == ["filed mid-cycle"]
    assert seen["id"] not in [r["id"] for r in left]


def test_folding_reports_in_does_not_buy_the_planner_another_cycle(substrate):
    # "New reports wake the PM once." The stored fingerprint is taken before the apply,
    # so without care the cleared reports look like a change the next beat must react
    # to — a second full Claude call about the planner's own work.
    _program(substrate)
    _hosts_yaml(substrate)
    _report(substrate)

    pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "folded"}])]), force=True)

    stored = substrate.load_pm_state("p1").last_fingerprint
    from coscience.pm_agent import context_fingerprint
    assert context_fingerprint(gather_context(substrate, "p1")) == stored


def test_two_writers_do_not_lose_a_report_between_them(substrate):
    # The dispatch loop, the PM loop and the HTTP server all write reports.json.
    import threading
    _program(substrate)
    done = []

    def file_one(n):
        substrate.add_host_report("p1", sprint_id=f"p1-s{n}", host="a",
                                  text=f"report {n}", source="finished", now=100.0 + n)
        done.append(n)

    threads_ = [threading.Thread(target=file_one, args=(n,)) for n in range(12)]
    for t in threads_:
        t.start()
    for t in threads_:
        t.join()
    assert len(done) == 12
    assert len(substrate.load_host_reports("p1")) == 12      # none lost to a race


def test_a_note_that_cannot_be_written_does_not_wedge_the_planner(substrate, monkeypatch):
    # An unhandled raise here leaves the staged cycle in place, so every later beat
    # re-applies it, hits the same error, and that program's planner never moves again.
    _program(substrate)
    _hosts_yaml(substrate)
    _report(substrate)

    def boom(*a, **kw):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(substrate, "save_host_note", boom)

    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "folded"}],
                      release_ids=[])]), force=True)

    assert summary["host_notes_updated"] == []
    assert summary["host_note_skipped"][0]["id"] == "a"
    assert "could not be written" in summary["host_note_skipped"][0]["why"]
    assert read_staging(substrate, "p1") is None          # the cycle finished and cleared


def test_a_note_a_human_saved_while_the_cycle_ran_is_not_overwritten(substrate):
    """O22: the planner read the note, a human saved a new one while it reasoned, and
    the planner's rewrite used to land over it without a word."""
    _program(substrate)
    _hosts_yaml(substrate)
    substrate.save_host_note("p1", "a", "as the planner read it")
    write_staging(substrate, "p1", 3,
                  PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "planner's"}]),
                  host_notes_seen={"a": "as the planner read it"})
    substrate.save_host_note("p1", "a", "the human's, saved mid-cycle")

    summary = pm_beat(substrate, "p1", FakeReasoner([]), force=True)

    assert substrate.load_host_note("p1", "a") == "the human's, saved mid-cycle"
    assert summary["host_note_skipped"] == [
        {"id": "a", "why": "a human edited it while this cycle ran"}]


def test_a_cycle_staged_before_notes_were_recorded_applies_as_before(substrate):
    _program(substrate)
    _hosts_yaml(substrate)
    substrate.save_host_note("p1", "a", "whatever it was")
    write_staging(substrate, "p1", 3,
                  PMCycleOutput(report="r", host_notes=[{"host": "a", "text": "planner's"}]))
    pm_beat(substrate, "p1", FakeReasoner([]), force=True)
    assert substrate.load_host_note("p1", "a") == "planner's"
