import json, time
from pathlib import Path
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus, BeatOutcome
from coscience.worker import Worker


class FakeAgent:
    """Agent that, on start, optionally writes a job.json (to simulate the worker
    declaring a detached job), and on collect returns a canned (text, status).
    When finished=True (and status ok) it also writes finished.json — the completion
    signal a well-behaved worker must emit for the sprint to be marked done."""
    def __init__(self, on_start=None, collect_result=("done text", "ok"), finished=True):
        self.on_start, self._collect, self.finished = on_start, collect_result, finished
        self.started, self.stopped, self.resumed = [], [], []
    def start(self, sprint, ctx, sprint_dir, repo_root=None):
        self.started.append(sprint.id)
        sprint_dir = Path(sprint_dir)
        sprint_dir.mkdir(parents=True, exist_ok=True)
        if self.on_start:
            self.on_start(sprint_dir)
        if self.finished and self._collect[1] == "ok":
            (sprint_dir / "finished.json").write_text("{}")
        return "agent-token"
    def resume(self, session_id, sprint_dir, nudge, model_slug="", repo_root=None):
        self.resumed.append(session_id)
        return "agent-token"
    def read_session_id(self, sprint_dir):
        return "fake-sess"
    def is_running(self, token):
        return False            # agent exits immediately after start
    def stop(self, token):
        self.stopped.append(token)
    def collect(self, sprint_dir):
        return self._collect


def _queued(sub, sid="s1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1"))


def test_ok_exit_with_live_job_stays_executing(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    def write_job(sprint_dir):
        (sprint_dir / "job.json").write_text(json.dumps(
            {"pid": 1, "out_file": "j.out", "expected_seconds": 5,
             "wake_after_seconds": 10, "max_seconds": 60, "note": "train"}))
    w = Worker(sub, FakeAgent(on_start=write_job, finished=False), job_alive=lambda t: True)
    w.run_one_beat()                       # claim -> launch agent
    out = w.run_one_beat()                 # agent exited + job.json declared
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.EXECUTING          # NOT done
    prog = sub.load_progress("s1")
    assert prog.job_token and prog.job_note == "train"
    assert not (sub.sprint_dir("s1") / "job.json").exists()   # consumed
    assert sp.results == []


def test_dead_job_relaunches_assess_then_done(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_out, prog.job_note = "1:1", "j.out", "train"
    prog.job_next_wake = time.time() + 9999; prog.job_max_seconds = 9999
    sub.save_sprint(sub.load_sprint("s1"))
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    sub.save_progress(prog)
    w = Worker(sub, FakeAgent(collect_result=("final findings", "ok")), job_alive=lambda t: False)
    w.run_sprint_beat(sub.load_sprint("s1"))   # job dead -> assess launch
    w.run_sprint_beat(sub.load_sprint("s1"))   # assess agent exits ok, no job -> done
    assert sub.load_sprint("s1").status == SprintStatus.DONE


def test_watchdog_terminates_overrun_job(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_out = "1:1", "j.out"
    prog.job_started_at = 0.0; prog.job_max_seconds = 1.0; prog.job_next_wake = 9e18
    sub.save_progress(prog)
    killed = []
    w = Worker(sub, FakeAgent(), job_alive=lambda t: True, terminate=lambda t: killed.append(t))
    w.run_sprint_beat(sub.load_sprint("s1"))
    assert killed == ["1:1"]
    assert sub.load_progress("s1").assess_reason == "timed out"


def test_malformed_job_json_ignored_and_removed(tmp_path):
    # Agent-authored job.json with non-numeric values must NOT crash the beat; the
    # poison file is dropped and the sprint completes normally.
    sub = Substrate(tmp_path); _queued(sub)
    def write_bad(sprint_dir):
        (sprint_dir / "job.json").write_text('{"pid": "abc", "expected_seconds": "soon"}')
    w = Worker(sub, FakeAgent(on_start=write_bad, collect_result=("final", "ok")),
               job_alive=lambda t: True)
    w.run_one_beat()                       # launch (writes bad job.json)
    w.run_one_beat()                       # exit ok, malformed job.json -> ignored -> done
    assert sub.load_sprint("s1").status == SprintStatus.DONE
    assert not (sub.sprint_dir("s1") / "job.json").exists()   # poison removed


def test_wake_relaunches_and_done_reaps_live_job(tmp_path):
    # On a wake with the job still alive, job_token is KEPT (watchdog stays armed);
    # when the assess run finishes without handling it, the done backstop kills it.
    sub = Substrate(tmp_path); _queued(sub)
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token = "1:1"; prog.job_out = "j.out"
    prog.job_started_at = time.time(); prog.job_max_seconds = 9e18
    prog.job_next_wake = 1.0                       # in the past -> wake now
    sub.save_progress(prog)
    killed = []
    w = Worker(sub, FakeAgent(collect_result=("assessed, all good", "ok")),
               job_alive=lambda t: True, terminate=lambda t: killed.append(t))
    w.run_sprint_beat(sub.load_sprint("s1"))      # wake: keep job_token, launch assess
    assert sub.load_progress("s1").job_token == "1:1"     # still tracked during assess
    w.run_sprint_beat(sub.load_sprint("s1"))      # assess exits ok, no new job -> done + reap
    assert sub.load_sprint("s1").status == SprintStatus.DONE
    assert killed == ["1:1"]                       # backstop killed the still-live job


def test_stale_job_json_cleared_on_launch(tmp_path):
    # A job.json left by a prior crashed attempt must be cleared at launch, so a
    # fresh clean run that declares no job isn't misattributed to the stale file.
    sub = Substrate(tmp_path); _queued(sub)
    d = sub.sprint_dir("s1"); d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps({"pid": 1, "out_file": "old.out", "note": "stale"}))
    w = Worker(sub, FakeAgent(collect_result=("real result", "ok")), job_alive=lambda t: True)
    w.run_one_beat()                       # launch -> clears the stale job.json
    assert not (d / "job.json").exists()
    w.run_one_beat()                       # exit ok, no job -> normal done
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.DONE
    assert "real result" in sub.load_result(sp.results[0]).summary
