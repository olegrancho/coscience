"""The completion contract: a clean exit is DONE only when the worker wrote
finished.json; a clean exit with neither finished.json nor job.json is the
premature-completion trap — the worker resumes the session to ask, and gives up
after a cap rather than laundering a premature message into a result."""
import json
from pathlib import Path

from tests.conftest import FakeAgent

from coscience.models import BeatOutcome, ProgressState, Sprint, SprintStatus
from coscience.worker import MAX_AMBIGUOUS_EXITS, Worker


def _queued(sub, sid="s1", program="p1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g",
                           plan=["a"], program=program))


def test_finished_json_summary_becomes_the_result(substrate):
    class WithSummary(FakeAgent):
        def start(self, sprint, ctx, sprint_dir, repo_root=None):
            tok = super().start(sprint, ctx, sprint_dir, repo_root)
            (Path(sprint_dir) / "finished.json").write_text(
                json.dumps({"summary": "the answer is 42"}))
            return tok

    sub = substrate
    _queued(sub)
    w = Worker(sub, WithSummary())
    w.run_one_beat()                       # launch
    assert w.run_one_beat() == BeatOutcome.COMPLETED
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.DONE
    assert "the answer is 42" in sub.load_result("s1-result").summary


def test_empty_finished_json_falls_back_to_final_message(substrate):
    # A sentinel with no summary still means done; the result is the final message.
    sub = substrate
    _queued(sub)
    w = Worker(sub, FakeAgent(result="final findings text"))   # writes empty {}
    w.run_one_beat()
    w.run_one_beat()
    assert sub.load_sprint("s1").status == SprintStatus.DONE
    assert "final findings text" in sub.load_result("s1-result").summary


def test_clean_exit_without_signal_resumes_instead_of_done(substrate):
    sub = substrate
    _queued(sub)
    agent = FakeAgent(finished=False, result="I'll stand by and wait")
    w = Worker(sub, agent)
    w.run_one_beat()                                    # launch
    out = w.run_one_beat()                              # clean exit, no signal
    assert out == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.EXECUTING          # NOT done
    assert sp.results == []
    prog = sub.load_progress("s1")
    assert prog.ambiguous_exits == 1
    assert prog.agent_session_id == "fake-sess"         # captured for --resume
    assert agent.resumed == ["fake-sess"]               # resumed the same session


def test_ambiguous_exits_cap_out_to_failed(substrate):
    sub = substrate
    _queued(sub)
    agent = FakeAgent(finished=False)
    w = Worker(sub, agent)
    w.run_one_beat()                                    # launch
    for _ in range(MAX_AMBIGUOUS_EXITS + 2):           # drive collect/resume to the cap
        w.run_one_beat()
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.FAILED
    prog = sub.load_progress("s1")
    assert prog.ambiguous_exits == MAX_AMBIGUOUS_EXITS
    assert "no completion signal" in prog.last_error
    assert prog.agent_token == ""


def test_ambiguous_exit_holds_when_usage_exhausted(substrate):
    sub = substrate
    _queued(sub)
    agent = FakeAgent(finished=False)
    w = Worker(sub, agent, usage_gate=lambda: True)
    w.run_one_beat()                                    # launch (usage ok)
    w._usage_gate = lambda: False                       # now exhausted
    out = w.run_one_beat()                              # ambiguous exit -> hold
    assert out == BeatOutcome.IDLE
    prog = sub.load_progress("s1")
    assert prog.ambiguous_exits == 0                    # not counted while held
    assert agent.resumed == []                          # not resumed
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING


def test_no_session_id_relaunches_fresh_instead_of_resume(substrate):
    class NoSess(FakeAgent):
        def read_session_id(self, sprint_dir):
            return ""

    sub = substrate
    _queued(sub)
    agent = NoSess(finished=False)
    w = Worker(sub, agent)
    w.run_one_beat()                                    # launch
    out = w.run_one_beat()                              # ambiguous, no session id
    assert out == BeatOutcome.PROGRESSED
    assert agent.resumed == []
    prog = sub.load_progress("s1")
    assert prog.agent_token == ""                       # cleared -> step 1 relaunches
    assert prog.ambiguous_exits == 1
    w.run_one_beat()                                    # fresh relaunch
    assert agent.started == ["s1", "s1"]


def test_stale_finished_json_cleared_on_launch(substrate):
    sub = substrate
    _queued(sub)
    d = sub.sprint_dir("s1")
    d.mkdir(parents=True, exist_ok=True)
    (d / "finished.json").write_text(json.dumps({"summary": "stale done"}))
    agent = FakeAgent(finished=False, result="fresh run, not done yet")
    w = Worker(sub, agent)
    w.run_one_beat()                                    # launch clears stale signal
    assert not (d / "finished.json").exists()
    w.run_one_beat()                                    # ambiguous -> resume, not done
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING
    assert agent.resumed == ["fake-sess"]


def test_progress_between_exits_resets_the_streak(substrate):
    # An agent that keeps advancing the scratchpad but never signals must NOT be
    # failed — only no-progress exits accumulate toward the cap.
    class Grows(FakeAgent):
        def __init__(self, **kw):
            super().__init__(finished=False, **kw)
            self._grow = 0

        def _bump(self, sprint_dir):
            self._grow += 1
            (Path(sprint_dir) / "scratchpad.md").write_text("x" * (self._grow * 100))

        def start(self, sprint, ctx, sprint_dir, repo_root=None):
            tok = super().start(sprint, ctx, sprint_dir, repo_root)
            self._bump(sprint_dir)
            return tok

        def resume(self, session_id, sprint_dir, nudge, model_slug="", repo_root=None):
            tok = super().resume(session_id, sprint_dir, nudge, model_slug, repo_root)
            self._bump(sprint_dir)
            return tok

    sub = substrate
    _queued(sub)
    agent = Grows()
    w = Worker(sub, agent)
    w.run_one_beat()                                   # launch (scratchpad grows)
    for _ in range(6):
        w.run_one_beat()                               # ambiguous but scratchpad grows each time
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING    # never failed
    prog = sub.load_progress("s1")
    assert prog.ambiguous_exits == 1                   # streak keeps resetting on progress
    assert len(agent.resumed) >= 3                      # kept resuming, not capping


def test_wake_assess_ambiguous_returns_to_sleeping(substrate):
    # A wake-assess run that ends without finishing/redeclaring must go back to
    # sleeping on the still-tracked job, not resume-nudge or count as ambiguous.
    import time
    sub = substrate
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token = "1:1"
    prog.job_out = "j.out"
    prog.job_started_at = time.time()
    prog.job_max_seconds = 9e18
    prog.job_next_wake = 1.0                            # in the past -> wake now
    sub.save_progress(prog)
    agent = FakeAgent(finished=False)                   # assess run exits ambiguous
    w = Worker(sub, agent, job_alive=lambda t: True)
    w.run_sprint_beat(sub.load_sprint("s1"))           # wake -> launch assess (job kept)
    assert sub.load_progress("s1").job_token == "1:1"
    w.run_sprint_beat(sub.load_sprint("s1"))           # assess ambiguous -> back to sleeping
    prog2 = sub.load_progress("s1")
    assert prog2.job_token == "1:1"                     # still tracked
    assert prog2.ambiguous_exits == 0                  # NOT counted
    assert agent.resumed == []                          # did NOT resume-nudge
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING


def test_progress_roundtrips_new_fields(substrate):
    substrate.save_progress(ProgressState(
        sprint_id="s1", agent_session_id="sess-xyz", ambiguous_exits=2, scratch_size=123))
    got = substrate.load_progress("s1")
    assert got.agent_session_id == "sess-xyz"
    assert got.ambiguous_exits == 2
    assert got.scratch_size == 123
