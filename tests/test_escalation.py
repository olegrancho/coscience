"""O8: an escalation holds the sprint with its lease and job, and never relaunches the agent."""
import json
import time
from pathlib import Path

from coscience import escalation, host_health
from coscience.models import BeatOutcome, ProgressState, Sprint, SprintStatus
from coscience.substrate import Substrate
from coscience.worker import Worker

RECORD = {"what": "gpu1 refuses SSH since 13:10; job 4121 state unknown",
          "tried": "three retries over 20 min", "may_have_broken_something": False,
          "needs": "a working host, or confirmation the job is still running"}


def _executing(substrate, sid="s1"):
    sp = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["a"], program="p1")
    substrate.save_sprint(sp)
    substrate.save_progress(ProgressState(sprint_id=sid))
    return sp


def test_escalate_json_is_read_and_validated(tmp_path):
    (tmp_path / "escalate.json").write_text(json.dumps(RECORD))
    assert escalation.read_escalate_json(tmp_path) == {**RECORD, "host_notes": ""}
    (tmp_path / "escalate.json").write_text("{not json")
    rec = escalation.read_escalate_json(tmp_path)
    assert rec["what"].startswith("escalate.json could not be read")
    (tmp_path / "escalate.json").unlink()
    assert escalation.read_escalate_json(tmp_path) is None


def test_raising_holds_the_sprint_and_asks_the_pm(substrate):
    sp = _executing(substrate)
    progress = substrate.load_progress("s1")
    escalation.raise_escalation(substrate, sp, progress, {**RECORD, "by": "agent", "host": "gpu1"}, now=100.0)
    sp, progress = substrate.load_sprint("s1"), substrate.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    th = sp.threads[-1]
    assert th["target"] == "pm" and th["kind"] == "escalation" and "refuses SSH" in th["messages"][0]["text"]
    assert progress.escalation["level"] == "pm" and progress.escalation["thread_id"] == th["id"]
    assert progress.escalation["by"] == "agent" and progress.escalation["host"] == "gpu1"


def test_a_second_escalation_after_a_pm_answer_goes_to_a_human(substrate):
    sp = _executing(substrate)
    progress = substrate.load_progress("s1")
    progress.pm_answered = True
    escalation.raise_escalation(substrate, sp, progress, {**RECORD, "by": "agent", "host": ""}, now=100.0)
    sp = substrate.load_sprint("s1")
    assert sp.threads[-1]["target"] == "human"
    assert substrate.load_progress("s1").escalation["level"] == "human"


def test_the_new_progress_fields_survive_a_round_trip(substrate):
    p = ProgressState(sprint_id="s1", escalation={"level": "pm", "what": "x"}, pm_answered=True,
                      resume_note="try host b", reallocate_to="b", stop_requested=True)
    substrate.save_progress(p)
    back = substrate.load_progress("s1")
    assert (back.escalation, back.pm_answered, back.resume_note, back.reallocate_to, back.stop_requested) == \
        ({"level": "pm", "what": "x"}, True, "try host b", "b", True)


# --- worker: the agent's own escalation holds the sprint --------------------------

from tests.test_worker_detached_job import FakeAgent


def _queued(sub, sid="s1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1"))


def test_an_agent_that_writes_escalate_json_is_held_not_relaunched(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)

    def write_escalate(sprint_dir):
        (sprint_dir / "escalate.json").write_text(json.dumps(RECORD))

    agent = FakeAgent(on_start=write_escalate, finished=False, collect_result=("gave up", "ok"))
    w = Worker(sub, agent)
    w.run_one_beat()                         # launch -> writes escalate.json, exits clean
    outcome = w.run_one_beat()                # collect -> escalate
    assert outcome == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    prog = sub.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    assert not (sub.sprint_dir("s1") / "escalate.json").exists()
    assert prog.agent_token == ""
    assert prog.escalation["by"] == "agent"

    started_before = list(agent.started)
    further = w.run_escalated_beat(sub.load_sprint("s1"))
    assert further == BeatOutcome.PROGRESSED
    assert agent.started == started_before                # never relaunched


def test_escalation_beats_still_collect_an_ended_job_but_wake_nobody(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.ESCALATED
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.escalation = {"by": "agent", "at": time.time(), "host": "gpu1", "level": "pm", "thread_id": "t1"}
    sub.save_progress(prog)

    class Slots:
        def release(self, sprint_id):
            pass

        def acquire(self, sprint_id):
            return True

        def gpus(self, sprint_id):
            return [], None

        def host(self, sprint_id):
            return {"name": "gpu1", "ssh": "gpu1", "run_root": "~/runs", "facts": "", "notes": ""}

        def ssh_for(self, host_name):
            return host_name if host_name == "gpu1" else ""

        def host_quiet(self, name):
            return False

    class Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, argv, stdin, timeout):
            self.calls.append(list(argv))
            if argv[0] == "ssh":
                return (0, "boot=boot-2\n", "")   # rebooted -> job gone
            return (0, "", "")

    runner = Runner()
    agent = FakeAgent()
    w = Worker(sub, agent, slots=Slots(), runner=runner)
    outcome = w.run_escalated_beat(sub.load_sprint("s1"))
    assert outcome == BeatOutcome.PROGRESSED
    assert any(c[0] == "rsync" for c in runner.calls)
    prog = sub.load_progress("s1")
    assert prog.job_token == "" and prog.job_host == ""
    assert sub.load_sprint("s1").status == SprintStatus.ESCALATED
    assert agent.started == []


def test_a_job_sleeping_on_a_quiet_host_raises_a_dispatcher_escalation(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = time.time(), 9e9, time.time() + 9e9
    sub.save_progress(prog)

    class Slots:
        def release(self, sprint_id):
            pass

        def acquire(self, sprint_id):
            return True

        def gpus(self, sprint_id):
            return [], None

        def host(self, sprint_id):
            return {"name": "gpu1", "ssh": "gpu1", "run_root": "~/runs", "facts": "", "notes": ""}

        def ssh_for(self, host_name):
            return host_name if host_name == "gpu1" else ""

        def host_quiet(self, name):
            return name == "gpu1"

    agent = FakeAgent()
    w = Worker(sub, agent, slots=Slots())
    outcome = w.run_sprint_beat(sub.load_sprint("s1"))
    assert outcome == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    prog = sub.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    assert prog.escalation["by"] == "dispatcher"
    assert "gpu1" in prog.escalation["what"]
    assert prog.job_token == "gpu1:4242:777:boot-1"        # job token kept
    assert agent.started == []


def test_a_stop_requested_escalated_sprint_fails_cleanly(tmp_path, monkeypatch):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.ESCALATED
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.escalation = {"by": "agent", "at": time.time(), "host": "", "level": "pm",
                       "thread_id": "t1", "what": "gpu1 refuses SSH"}
    prog.stop_requested = True
    sub.save_progress(prog)

    released = []
    from coscience import artifacts as artifacts_mod
    monkeypatch.setattr(artifacts_mod, "release_for_sprint",
                        lambda substrate, sprint, now: released.append(sprint.id))

    w = Worker(sub, FakeAgent())
    outcome = w.run_escalated_beat(sub.load_sprint("s1"))
    assert outcome == BeatOutcome.COMPLETED
    sp = sub.load_sprint("s1")
    prog = sub.load_progress("s1")
    assert sp.status == SprintStatus.FAILED
    assert "escalat" in prog.last_error
    assert released == ["s1"]


def test_the_agent_is_told_how_to_escalate():
    from coscience.claude_executor import build_instructions
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    text = build_instructions(sprint, None, Path("/tmp/s1/scratchpad.md"))
    assert "escalate.json" in text and "may_have_broken_something" in text


# --- dispatcher: an escalated sprint is beaten and renewed, never granted ---------

def _dispatcher(substrate, capacity, agent=None):
    from coscience.dispatcher import Dispatcher
    from coscience.resources import ResourcePool
    from coscience.scheduler import SchedulerPolicy
    return Dispatcher(substrate, agent or FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def test_an_escalated_sprint_with_a_lease_is_beaten_renewed_and_not_reconciled(substrate, monkeypatch):
    sp = Sprint(id="s1", status=SprintStatus.ESCALATED, goals="g", plan=["a"],
               resources_required={"gpu": 1.0})
    substrate.save_sprint(sp)
    prog = substrate.load_progress("s1")
    prog.escalation = {"by": "agent", "at": 0.0, "host": "", "level": "pm", "thread_id": "t1"}
    substrate.save_progress(prog)

    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.ledger.acquire("s1", {"gpu": 1.0}, now=0.0, ttl=60.0)
    disp.ledger.save()

    calls = []

    def spy(sprint):
        calls.append(sprint.id)
        return BeatOutcome.PROGRESSED
    monkeypatch.setattr(disp.worker, "run_escalated_beat", spy)

    report = disp.run_one_cycle(now=10.0)
    assert calls == ["s1"]
    disp.ledger.load()
    lease = disp.ledger.lease_for("s1")
    assert lease is not None and lease.expires_at > 10.0          # renewed
    assert report.reconciled == 0
    assert substrate.load_sprint("s1").status == SprintStatus.ESCALATED   # untouched


def test_a_leaseless_escalated_sprint_is_not_granted_and_not_counted(substrate):
    sp = Sprint(id="s1", status=SprintStatus.ESCALATED, goals="g", plan=["a"])
    substrate.save_sprint(sp)
    prog = substrate.load_progress("s1")
    prog.escalation = {"by": "agent", "at": 0.0, "host": "", "level": "pm", "thread_id": "t1"}
    substrate.save_progress(prog)

    disp = _dispatcher(substrate, {"gpu": 1.0})
    report = disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is None            # never granted
    assert report.waiting == 0
    assert "s1" not in report.unrunnable


# --- Fix round 1 ------------------------------------------------------------------
#
# Critical 1: a failed exit that wrote escalate.json must escalate (not be counted
# as a retriable failure), and a stale escalate.json left by an earlier attempt must
# be cleared at launch just like job.json/finished.json.

def test_a_failed_exit_that_escalated_is_not_counted_as_a_failure(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)

    def write_escalate(sprint_dir):
        (sprint_dir / "escalate.json").write_text(json.dumps(RECORD))

    agent = FakeAgent(on_start=write_escalate, finished=False, collect_result=("boom", "failed"))
    w = Worker(sub, agent)
    w.run_one_beat()                          # launch -> writes escalate.json, exits nonzero
    outcome = w.run_one_beat()                 # collect -> escalate, not a counted failure
    assert outcome == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    prog = sub.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    assert prog.failures == 0
    assert not (sub.sprint_dir("s1") / "escalate.json").exists()


def test_a_stale_escalate_json_is_cleared_at_launch(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    d = sub.sprint_dir("s1")
    d.mkdir(parents=True, exist_ok=True)
    (d / "escalate.json").write_text(json.dumps(RECORD))
    agent = FakeAgent(collect_result=("real result", "ok"))
    w = Worker(sub, agent)
    w.run_one_beat()                          # launch -> clears the stale escalate.json
    assert not (d / "escalate.json").exists()
    w.run_one_beat()                          # exit ok, no escalate.json -> normal done
    assert sub.load_sprint("s1").status == SprintStatus.DONE


# Critical 2: an ESCALATED sprint must never be picked as a yield victim.

def test_is_yieldable_is_false_for_an_escalated_sprint(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.ESCALATED, goals="g", plan=["a"]))
    w = Worker(substrate, FakeAgent())
    assert w.is_yieldable("s1") is False


def test_an_escalated_sprint_is_never_hibernated_to_make_room(substrate):
    esc = Sprint(id="esc", status=SprintStatus.ESCALATED, goals="g", plan=["a"],
                resources_required={"gpu": 1.0}, priority=0, preemptible=True)
    substrate.save_sprint(esc)
    prog = substrate.load_progress("esc")
    prog.escalation = {"by": "agent", "at": 0.0, "host": "", "level": "pm", "thread_id": "t1"}
    substrate.save_progress(prog)

    hi = Sprint(id="hi", status=SprintStatus.QUEUED, goals="g", plan=["a"],
               resources_required={"gpu": 1.0}, priority=9)
    substrate.save_sprint(hi)

    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.ledger.acquire("esc", {"gpu": 1.0}, now=0.0, ttl=600.0, priority=0, preemptible=True)
    disp.ledger.save()

    report = disp.run_one_cycle(now=1.0)
    assert report.hibernated == 0
    disp.ledger.load()
    assert disp.ledger.lease_for("esc") is not None
    assert substrate.load_sprint("esc").status == SprintStatus.ESCALATED
    assert disp.ledger.lease_for("hi") is None            # candidate still waits: nothing was freed


# Important 3: a job declared in the same turn as an escalation must be tracked, not
# orphaned; a finished.json in the same turn must not win over the escalation.

def test_escalate_with_a_declared_job_tracks_the_job(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)

    def write_both(sprint_dir):
        (sprint_dir / "escalate.json").write_text(json.dumps(RECORD))
        (sprint_dir / "job.json").write_text(json.dumps(
            {"pid": 1, "out_file": "j.out", "note": "train",
             "expected_seconds": 5, "wake_after_seconds": 10, "max_seconds": 60}))

    agent = FakeAgent(on_start=write_both, finished=False, collect_result=("ok text", "ok"))
    w = Worker(sub, agent)
    w.run_one_beat()
    outcome = w.run_one_beat()
    assert outcome == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    prog = sub.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    assert prog.job_token and prog.job_note == "train"
    assert not (sub.sprint_dir("s1") / "job.json").exists()


def test_escalate_with_finished_json_does_not_complete_the_sprint(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)

    def write_both(sprint_dir):
        (sprint_dir / "escalate.json").write_text(json.dumps(RECORD))
        (sprint_dir / "finished.json").write_text(json.dumps({"summary": "done!"}))

    agent = FakeAgent(on_start=write_both, finished=False, collect_result=("ok text", "ok"))
    w = Worker(sub, agent)
    w.run_one_beat()
    outcome = w.run_one_beat()
    assert outcome == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.ESCALATED
    assert sp.status != SprintStatus.DONE


# --- Task 2: an answer resumes or moves the sprint --------------------------------

from coscience.resources import ResourcePool

POOL = ResourcePool.from_dict({"cpu": 4, "hosts": {"gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
                                                    "gpu2": {"ssh": "gpu2", "capacity": {"cpu": 8},
                                                             "programs": ["other"]},
                                                    "gpu3": {"ssh": "gpu3", "capacity": {"cpu": 8}}}})


def _escalated(substrate, level="pm", host="gpu1"):
    sp = _executing(substrate)
    progress = substrate.load_progress("s1")
    progress.host = host
    escalation.raise_escalation(substrate, sp, progress, {**RECORD, "by": "agent", "host": host}, now=1.0)
    if level == "human":
        progress = substrate.load_progress("s1")
        progress.escalation["level"] = "human"
        substrate.save_progress(progress)
    return substrate.load_sprint("s1")


def test_a_pm_resume_hands_instructions_to_the_next_run(substrate):
    _escalated(substrate)
    assert escalation.answer(substrate, "s1", "resume", instructions="the job is fine; wait for it", by="pm") == ""
    sp, progress = substrate.load_sprint("s1"), substrate.load_progress("s1")
    assert sp.status == SprintStatus.EXECUTING
    assert progress.resume_note == "the job is fine; wait for it" and progress.pm_answered
    assert progress.escalation == {}
    th = sp.threads[-1]
    assert th["messages"][-1]["role"] == "pm" and "resume" in th["messages"][-1]["text"].lower()


def test_a_pm_reallocation_must_name_another_host_the_program_may_use(substrate, every_host_placeable):
    _escalated(substrate)
    assert "same host" in escalation.answer(substrate, "s1", "reallocate", host="gpu1", by="pm", pool=POOL)
    assert "may not use" in escalation.answer(substrate, "s1", "reallocate", host="gpu2", by="pm", pool=POOL)
    assert "no host" in escalation.answer(substrate, "s1", "reallocate", host="nope", by="pm", pool=POOL)
    assert escalation.answer(substrate, "s1", "reallocate", host="gpu3", instructions="use gpu3", by="pm", pool=POOL) == ""
    progress = substrate.load_progress("s1")
    assert progress.reallocate_to == "gpu3" and substrate.load_sprint("s1").status == SprintStatus.EXECUTING


def test_to_human_keeps_it_escalated_and_retargets_the_thread(substrate):
    _escalated(substrate)
    assert escalation.answer(substrate, "s1", "to_human", instructions="needs someone on the machine", by="pm") == ""
    sp, progress = substrate.load_sprint("s1"), substrate.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED and progress.escalation["level"] == "human"
    assert sp.threads[-1]["target"] == "human"


def test_the_pm_cannot_answer_a_human_level_escalation_or_stop_a_sprint(substrate):
    _escalated(substrate, level="human")
    assert "human" in escalation.answer(substrate, "s1", "resume", by="pm")
    _ = substrate
    assert "only a human" in escalation.answer(substrate, "s1", "stop", by="pm")
    assert escalation.answer(substrate, "s1", "stop", by="oleg") == ""
    assert substrate.load_progress("s1").stop_requested


def test_an_answer_to_a_sprint_that_is_not_escalated_is_refused(substrate):
    _executing(substrate)
    assert "not escalated" in escalation.answer(substrate, "s1", "resume", by="pm")


def test_an_answer_to_a_missing_sprint_is_refused(substrate):
    assert escalation.answer(substrate, "nope", "resume", by="pm") == "no such sprint"


class CapturingAgent(FakeAgent):
    """Records the context each launch received (as O6's test_remote_jobs.py)."""

    def __init__(self):
        super().__init__(finished=False)
        self.contexts = []

    def start(self, sprint, ctx, sprint_dir, repo_root=None):
        self.contexts.append(ctx)
        return super().start(sprint, ctx, sprint_dir, repo_root)


def test_a_resume_note_is_handed_to_the_next_run_and_then_cleared(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.resume_note = "use gpu3"
    sub.save_progress(prog)

    agent = CapturingAgent()
    w = Worker(sub, agent)
    w.run_one_beat()               # launch -> ctx carries the resume note
    assert agent.contexts[-1].resume_note == "use gpu3"
    assert sub.load_progress("s1").resume_note == ""


def _relocate_slots():
    class Slots:
        def release(self, sprint_id):
            pass

        def acquire(self, sprint_id):
            return True

        def gpus(self, sprint_id):
            return [], None

        def host(self, sprint_id):
            return {"name": "gpu1", "ssh": "gpu1", "run_root": "~/runs", "facts": "", "notes": ""}

        def ssh_for(self, host_name):
            return host_name if host_name == "gpu1" else ""

        def host_quiet(self, name):
            return False
    return Slots()


def test_relocate_moves_the_sprint_to_the_new_host(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.host = "gpu1"
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.reallocate_to = "gpu3"
    prog.agent_session_id = "abc"
    sub.save_progress(prog)

    class Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, argv, stdin, timeout):
            self.calls.append(list(argv))
            return (0, "", "")

    runner = Runner()
    w = Worker(sub, FakeAgent(), slots=_relocate_slots(), runner=runner)
    w.relocate(sub.load_sprint("s1"))

    assert any(c[0] == "ssh" for c in runner.calls)            # terminated on gpu1
    assert any(c[0] == "rsync" for c in runner.calls)          # collected before the move
    prog = sub.load_progress("s1")
    assert prog.host == "gpu3"
    assert prog.job_token == "" and prog.job_host == "" and prog.job_collect == []
    assert prog.agent_token == "" and prog.agent_session_id == ""
    assert prog.reallocate_to == ""
    assert "gpu3" in prog.resume_note and "start fresh" in prog.resume_note


# --- Fix round 1 --------------------------------------------------------------
#
# Important: relocate must not swallow a failed remote stop — an old job that
# could not be verified as stopped must be surfaced in collect_note, not silently
# left running while a fresh agent starts on the new host.

def test_relocate_notes_a_remote_stop_that_could_not_be_verified(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.host = "gpu1"
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242::", "gpu1", []
    prog.reallocate_to = "gpu3"
    sub.save_progress(prog)

    class Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, argv, stdin, timeout):
            self.calls.append(list(argv))
            return (0, "", "")

    runner = Runner()
    w = Worker(sub, FakeAgent(), slots=_relocate_slots(), runner=runner)
    w.relocate(sub.load_sprint("s1"))

    assert not any(c[0] == "ssh" for c in runner.calls)        # no kill command was sent
    prog = sub.load_progress("s1")
    assert prog.host == "gpu3"
    assert "could not stop the job on gpu1" in prog.collect_note


def test_a_reallocate_answer_is_carried_out_by_the_dispatcher_before_the_next_beat(substrate, monkeypatch):
    sp = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"],
               resources_required={"gpu": 1.0})
    substrate.save_sprint(sp)
    prog = substrate.load_progress("s1")
    prog.host = "gpu1"
    prog.reallocate_to = "gpu3"
    substrate.save_progress(prog)

    disp = _dispatcher(substrate, {"gpu": 1.0})
    disp.ledger.acquire("s1", {"gpu": 1.0}, now=0.0, ttl=60.0)
    disp.ledger.save()

    calls = []

    def spy(sprint):
        calls.append(sprint.id)
    monkeypatch.setattr(disp.worker, "relocate", spy)

    disp.run_one_cycle(now=10.0)
    assert calls == ["s1"]
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is None             # released, not renewed
    assert substrate.load_sprint("s1").status == SprintStatus.EXECUTING


# --- Final-review fix wave -----------------------------------------------------
#
# Fix A: a leaseless ESCALATED sprint (dispatcher outage past the lease TTL) must
# not be forgotten — a job it holds is re-adopted, and a pending human stop is
# still carried out even with no lease to beat through the normal loop.

def test_a_leaseless_escalated_sprint_with_a_job_is_re_leased_and_beaten(substrate, monkeypatch):
    sp = Sprint(id="s1", status=SprintStatus.ESCALATED, goals="g", plan=["a"])
    substrate.save_sprint(sp)
    prog = substrate.load_progress("s1")
    prog.escalation = {"by": "agent", "at": 0.0, "host": "", "level": "pm", "thread_id": "t1"}
    prog.job_token = "4242:777"
    prog.host = "local"
    substrate.save_progress(prog)

    disp = _dispatcher(substrate, {"cpu": 4})
    monkeypatch.setattr(disp.worker, "agent_running", lambda sid: False)
    calls = []

    def spy(sprint):
        calls.append(sprint.id)
        return BeatOutcome.PROGRESSED
    monkeypatch.setattr(disp.worker, "run_escalated_beat", spy)

    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is not None          # re-leased
    assert calls == ["s1"]                                  # beaten the same cycle
    assert substrate.load_sprint("s1").status == SprintStatus.ESCALATED   # status untouched


def test_a_leaseless_escalated_sprint_with_a_pending_stop_fails_within_one_cycle(substrate):
    sp = Sprint(id="s1", status=SprintStatus.ESCALATED, goals="g", plan=["a"])
    substrate.save_sprint(sp)
    prog = substrate.load_progress("s1")
    prog.escalation = {"by": "agent", "at": 0.0, "host": "", "level": "human",
                       "thread_id": "t1", "what": "x"}
    prog.stop_requested = True
    substrate.save_progress(prog)

    disp = _dispatcher(substrate, {"cpu": 4})
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is None
    assert substrate.load_sprint("s1").status == SprintStatus.FAILED


# --- Fix B: a reallocation cannot strand the sprint ----------------------------

def test_move_targets_excludes_current_drained_and_quiet_hosts(substrate, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
        "gpu2": {"ssh": "gpu2", "capacity": {"cpu": 8}, "drain": True},
        "gpu3": {"ssh": "gpu3", "capacity": {"cpu": 8}},
    }})
    host_health._save(substrate.repo_root, {
        "gpu3": {"checked_at": 1.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "down"}})
    targets = escalation.move_targets(pool, None, "gpu1", substrate.repo_root)
    assert set(targets) == {"local"}


def test_move_targets_excludes_a_marked_for_removal_host(substrate, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
        "gpu2": {"ssh": "gpu2", "capacity": {"cpu": 8}, "remove": True},
    }})
    targets = escalation.move_targets(pool, None, "gpu1", substrate.repo_root)
    assert set(targets) == {"local"}


def test_reallocate_to_a_drained_host_is_refused(substrate, every_host_placeable):
    drain_pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
        "gpu4": {"ssh": "gpu4", "capacity": {"cpu": 8}, "drain": True},
    }})
    _escalated(substrate, host="gpu1")
    msg = escalation.answer(substrate, "s1", "reallocate", host="gpu4", by="pm", pool=drain_pool)
    # M6 (fix round 1): mentions "being removed" too, now that Remove is a mark, not
    # an instant deletion.
    assert msg == "gpu4 takes no new work right now (being removed, drained or not answering)"


def test_a_leaseless_sprint_with_reallocate_to_is_pinned_to_the_new_host(
        substrate, every_host_placeable, monkeypatch):
    sp = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"])
    substrate.save_sprint(sp)
    prog = substrate.load_progress("s1")
    prog.host = "gpu1"
    prog.reallocate_to = "gpu3"
    substrate.save_progress(prog)

    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
        "gpu3": {"ssh": "gpu3", "capacity": {"cpu": 8}},
    }})
    from coscience.dispatcher import Dispatcher
    from coscience.scheduler import SchedulerPolicy
    disp = Dispatcher(substrate, FakeAgent(), pool, SchedulerPolicy(aging_interval=0.0))

    hosts_used = []
    orig_acquire = disp.ledger.acquire

    def spy(sprint_id, amounts, now, ttl, **kw):
        hosts_used.append(kw.get("host"))
        return orig_acquire(sprint_id, amounts, now, ttl, **kw)
    monkeypatch.setattr(disp.ledger, "acquire", spy)
    monkeypatch.setattr(disp.worker, "relocate", lambda sprint: None)   # isolate the grant step

    disp.run_one_cycle(now=0.0)
    assert hosts_used == ["gpu3"]


# --- Fix C: the escalated beat never overwrites a concurrent answer -------------

def _collect_slots(host="gpu1"):
    class Slots:
        def release(self, sprint_id):
            pass

        def acquire(self, sprint_id):
            return True

        def gpus(self, sprint_id):
            return [], None

        def host(self, sprint_id):
            return {"name": host, "ssh": host, "run_root": "~/runs", "facts": "", "notes": ""}

        def ssh_for(self, host_name):
            return host_name if host_name == host else ""

        def host_quiet(self, name):
            return False
    return Slots()


def test_run_escalated_beat_does_not_clobber_a_concurrent_answer(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.host = "gpu1"
    sub.save_progress(prog)
    s, prog = sub.load_sprint("s1"), sub.load_progress("s1")
    escalation.raise_escalation(sub, s, prog, {**RECORD, "by": "agent", "host": "gpu1"}, now=1.0)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    sub.save_progress(prog)

    w = Worker(sub, FakeAgent(), slots=_collect_slots())
    w._job_alive = lambda token: False

    def fake_collect(progress, sprint_dir):
        # Simulate the concurrent PM answer landing while the (slow) collect runs.
        assert escalation.answer(sub, "s1", "resume", instructions="go", by="pm") == ""
    w._collect_job = fake_collect

    outcome = w.run_escalated_beat(sub.load_sprint("s1"))
    assert outcome == BeatOutcome.PROGRESSED
    prog = sub.load_progress("s1")
    assert prog.resume_note == "go"
    assert prog.escalation == {}
    assert prog.job_token == "" and prog.job_host == "" and prog.job_collect == []
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING


def test_a_stop_beat_skips_setting_failed_if_the_sprint_moved_on(tmp_path, monkeypatch):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.ESCALATED
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.escalation = {"by": "agent", "at": 0.0, "host": "", "level": "human",
                       "thread_id": "t1", "what": "x"}
    prog.stop_requested = True
    sub.save_progress(prog)

    w = Worker(sub, FakeAgent())

    def fake_stop(sprint):
        # A second dispatcher instance already moved this sprint on while this
        # one was mid-stop.
        moved = sub.load_sprint("s1")
        moved.status = SprintStatus.EXECUTING
        sub.save_sprint(moved)
        return []
    monkeypatch.setattr(w, "stop_sprint", fake_stop)

    outcome = w.run_escalated_beat(sub.load_sprint("s1"))
    assert outcome == BeatOutcome.PROGRESSED
    assert sub.load_sprint("s1").status == SprintStatus.EXECUTING   # not clobbered to FAILED


def test_a_quiet_host_escalation_fires_even_with_a_stale_escalation_dict(tmp_path):
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = time.time(), 9e9, time.time() + 9e9
    prog.escalation = {"level": "pm", "what": "an old, already-resolved escalation"}   # stale
    sub.save_progress(prog)

    slots = _collect_slots()
    slots.host_quiet = lambda name: name == "gpu1"
    agent = FakeAgent()
    w = Worker(sub, agent, slots=slots)
    outcome = w.run_sprint_beat(sub.load_sprint("s1"))
    assert outcome == BeatOutcome.PROGRESSED
    sp = sub.load_sprint("s1")
    prog = sub.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    assert prog.escalation["by"] == "dispatcher"       # the NEW escalation overwrote the stale one
    assert agent.started == []


def test_the_quiet_escalation_wording_follows_the_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(host_health, "QUIET_AFTER", 600.0)   # 10 minutes
    sub = Substrate(tmp_path)
    _queued(sub)
    s = sub.load_sprint("s1")
    s.status = SprintStatus.EXECUTING
    sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = "gpu1:4242:777:boot-1", "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = time.time(), 9e9, time.time() + 9e9
    sub.save_progress(prog)

    slots = _collect_slots()
    slots.host_quiet = lambda name: name == "gpu1"
    agent = FakeAgent()
    w = Worker(sub, agent, slots=slots)
    w.run_sprint_beat(sub.load_sprint("s1"))
    prog = sub.load_progress("s1")
    assert "10 minutes" in prog.escalation["what"]


# --- Fix D: a pending stop cannot be overridden ---------------------------------

def test_answer_after_a_stop_is_pending_is_refused(substrate):
    _escalated(substrate)
    assert escalation.answer(substrate, "s1", "stop", by="oleg") == ""
    assert substrate.load_progress("s1").stop_requested
    msg = escalation.answer(substrate, "s1", "resume", instructions="go", by="oleg")
    assert "stop is already pending" in msg


def test_a_stale_thread_id_is_refused(substrate):
    _escalated(substrate)
    msg = escalation.answer(substrate, "s1", "resume", by="pm", thread_id="not-the-real-one")
    assert "earlier escalation" in msg
