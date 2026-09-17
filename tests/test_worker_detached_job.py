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


# --- the worker slot a sleeping sprint must not hold ----------------------------
#
# `effective_requirement` charges every sprint one worker slot for the whole life
# of its lease, and its docstring says that "bounds the number of agent processes
# running at once". It does not — it bounds LEASES. p5-c26 held the substrate's
# only slot (`workers: 1.0`) for 15 hours while a detached GPU job ran and no
# agent process existed, so nothing else could be dispatched. The slot has to be
# charged for the agent, not for the lease.

class FakeSlots:
    """The dispatcher's worker-slot handle, as the Worker sees it."""

    def __init__(self, free=1):
        self.free, self.held, self.calls = free, set(), []

    def release(self, sprint_id):
        self.calls.append(("release", sprint_id))
        if sprint_id in self.held:
            self.held.discard(sprint_id)
            self.free += 1

    def acquire(self, sprint_id):
        self.calls.append(("acquire", sprint_id))
        if sprint_id in self.held:
            return True                       # idempotent: already ours
        if self.free <= 0:
            return False
        self.free -= 1
        self.held.add(sprint_id)
        return True

    def gpus(self, sprint_id):
        return [], None

    def host(self, sprint_id):
        return {"name": "local", "ssh": "", "run_root": "", "facts": "", "notes": ""}

    def ssh_for(self, host_name):
        return ""

    def host_quiet(self, name):
        return False


def _sleeping(sub, sid="s1", *, next_wake):
    s = sub.load_sprint(sid); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress(sid)
    prog.job_token, prog.job_out, prog.job_note = "1:1", "j.out", "train"
    prog.job_started_at = time.time(); prog.job_max_seconds = 9e9
    prog.job_next_wake = next_wake
    sub.save_progress(prog)


def test_a_sprint_sleeping_on_a_job_gives_up_its_worker_slot(tmp_path):
    """15 hours of GPU training with no agent alive must not block dispatch."""
    sub = Substrate(tmp_path); _queued(sub)
    _sleeping(sub, next_wake=time.time() + 9999)      # not due yet
    slots = FakeSlots(free=0)
    slots.held.add("s1")                              # granted with the slot
    w = Worker(sub, FakeAgent(), job_alive=lambda t: True, slots=slots)

    assert w.run_sprint_beat(sub.load_sprint("s1")) == BeatOutcome.PROGRESSED
    assert slots.free == 1                            # handed back
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING   # still holds the GPU


def test_a_sprint_due_to_wake_takes_a_slot_back_before_launching(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    _sleeping(sub, next_wake=time.time() - 1)         # due
    slots = FakeSlots(free=1)
    agent = FakeAgent(collect_result=("findings", "ok"))
    w = Worker(sub, agent, job_alive=lambda t: True, slots=slots)

    w.run_sprint_beat(sub.load_sprint("s1"))
    assert agent.started == ["s1"]                    # the assess agent ran
    assert slots.free == 0 and "s1" in slots.held


def test_a_woken_sprint_with_no_free_slot_waits_instead_of_launching(tmp_path):
    """This is the "treat it as queued" half. The job keeps running and the sprint
    keeps its lease; it just cannot start an agent until a slot frees, exactly as a
    queued sprint cannot."""
    sub = Substrate(tmp_path); _queued(sub)
    _sleeping(sub, next_wake=time.time() - 1)         # due
    slots = FakeSlots(free=0)                         # someone else has it
    agent = FakeAgent()
    w = Worker(sub, agent, job_alive=lambda t: True, slots=slots)

    assert w.run_sprint_beat(sub.load_sprint("s1")) == BeatOutcome.PROGRESSED
    assert agent.started == []                        # did NOT launch
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING
    assert sub.load_progress("s1").job_token == "1:1"  # job untouched


def test_without_a_slot_handle_nothing_changes(tmp_path):
    """The Worker is constructed directly in tests and by non-dispatcher callers;
    absent a handle it must behave exactly as before."""
    sub = Substrate(tmp_path); _queued(sub)
    _sleeping(sub, next_wake=time.time() + 9999)
    w = Worker(sub, FakeAgent(), job_alive=lambda t: True)
    assert w.run_sprint_beat(sub.load_sprint("s1")) == BeatOutcome.PROGRESSED
