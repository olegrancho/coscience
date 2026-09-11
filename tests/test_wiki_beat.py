from pathlib import Path

import pytest

from coscience import wiki, wiki_store
from coscience.models import Program, ProgramStatus, Result, Sprint, SprintStatus


class FakeWikiAgent:
    """Records launches; never starts a process."""

    def __init__(self):
        self.launches = []
        self.alive = True
        self.report = {}

    def launch(self, *, kind, program, bundle, run_dir, objects=None, report="",
               model=""):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.launches.append({"kind": kind, "objects": [o.oid for o, _ in (objects or [])],
                              "model": model, "run_dir": run_dir, "report": report})
        return f"tok{len(self.launches)}"

    def is_running(self, token):
        return self.alive

    def collect(self, run_dir):
        from coscience import wiki_agent
        return wiki_agent.WikiAgent().collect(run_dir)


@pytest.fixture
def agent2():
    return FakeWikiAgent()


def _seed_result(substrate, rid, sid, pid, at):
    substrate.save_sprint(Sprint(id=sid, status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id=rid, sprint=sid, summary=f"s {rid}", completed_at=at))


def test_disabled_program_does_nothing(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", wiki_enabled=False)
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_non_active_program_does_nothing(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", status=ProgramStatus.PAUSED)
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_nothing_pending_returns_empty(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_launch_ingests_the_oldest_batch_and_records_the_run(substrate, agent2, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "2")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    for i, at in enumerate([30.0, 10.0, 20.0]):
        _seed_result(substrate, f"r{i}", f"s{i}", "p1", at)
    line = wiki.beat(substrate, p, 100.0, agent2)
    assert line.startswith("wiki: launched ingest")
    assert agent2.launches[0]["objects"] == ["result:r1", "result:r2"]  # oldest first
    state = wiki_store.load_state(substrate, "p1")
    assert state["run"]["kind"] == "ingest"
    assert state["run"]["batch"] == ["result:r1", "result:r2"]
    assert state["run"]["token"] == "tok1"
    assert state["run"]["started_at"] == 100.0


def test_launch_creates_the_bundle(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    assert (wiki_store.bundle_dir(substrate, "p1") / "CLAUDE.md").is_file()


def test_launch_uses_the_programs_wiki_model(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", wiki_model="claude-haiku-4-5-20251001")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    assert agent2.launches[0]["model"] == "claude-haiku-4-5-20251001"


def test_only_one_run_at_a_time(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    agent2.alive = True
    assert wiki.beat(substrate, p, 110.0, agent2) == "wiki: running"
    assert len(agent2.launches) == 1


def test_usage_gate_blocks_launch(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2, usage_gate=lambda: False) == ""
    assert agent2.launches == []


def test_pause_blocks_launch(substrate, agent2):
    from coscience import pause
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    pause.set_paused(substrate.repo_root, True)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_lint_runs_after_the_cadence_and_only_on_a_non_empty_bundle(substrate, agent2,
                                                                    monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "2")
    from coscience import wiki_okf
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    wiki_store.ensure_bundle(substrate, "p1")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 2
    # empty bundle: nothing to lint, and nothing pending either
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="# Definition\n\nx\n"))
    line = wiki.beat(substrate, p, 110.0, agent2)
    assert line.startswith("wiki: launched lint")
    assert agent2.launches[-1]["kind"] == "lint"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 2  # not reset yet


def test_run_ids_increment(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    first = wiki_store.load_state(substrate, "p1")["run"]["id"]
    assert first == "r0001"
    assert (wiki_store.state_dir(substrate, "p1") / "runs" / first).is_dir()


# --- J3: holding a short batch so the fixed prefix is amortised --------------
#
# A run re-reads its ~14k-token prefix on every one of its ~35 turns, so the
# fixed cost is paid per RUN and the marginal cost per object is small: measured
# across 14 real runs, an object costs $1.38 when it is ingested alone and $0.73
# when it rides in a batch of four. Holding a lone object for company is the
# lever. The hold is OFF unless a max-wait is set, because results on this
# substrate land a median of 5.5-24.6h apart — a hold long enough to fill a
# batch of four would leave the wiki days stale, which is the opposite of what
# I1 (ingest when a sprint finishes) is for.

def test_the_hold_is_off_unless_a_max_wait_is_configured(substrate, agent2):
    """The default must not delay anything. At the real arrival rate a hold long
    enough to fill a batch costs days of staleness to save cents, so waiting is
    opt-in and today's behaviour is what you get when the knob is unset."""
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2).startswith("wiki: launched ingest")


def test_a_lone_object_waits_for_company_while_the_deadline_holds(substrate, agent2, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "4")
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_WAIT", "600")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)

    line = wiki.beat(substrate, p, 100.0, agent2)
    assert agent2.launches == []
    assert "holding" in line and "1/4" in line
    # Armed once, and the clock runs from the first beat that saw the object.
    assert wiki_store.load_state(substrate, "p1")["batch_armed_at"] == 100.0
    # Quiet from here: the dispatcher commits the substrate on any cycle a wiki
    # beat speaks, and it beats every few seconds — an hours-long hold that
    # announced itself each time would commit every few seconds for hours.
    assert wiki.beat(substrate, p, 200.0, agent2) == ""
    assert wiki_store.load_state(substrate, "p1")["batch_armed_at"] == 100.0
    assert agent2.launches == []


def test_a_held_batch_launches_when_the_deadline_passes(substrate, agent2, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "4")
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_WAIT", "600")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)

    wiki.beat(substrate, p, 100.0, agent2)
    assert wiki.beat(substrate, p, 701.0, agent2).startswith("wiki: launched ingest")
    assert agent2.launches[0]["objects"] == ["result:r1"]
    # The arm is spent, so the next batch starts its own clock.
    assert "batch_armed_at" not in wiki_store.load_state(substrate, "p1")


def test_a_full_batch_never_waits(substrate, agent2, monkeypatch):
    """The hold exists to fill a batch. Once it is full there is nothing to wait
    for, and waiting would be pure staleness."""
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "2")
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_WAIT", "99999")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 10.0)
    _seed_result(substrate, "r2", "s2", "p1", 20.0)

    assert wiki.beat(substrate, p, 100.0, agent2).startswith("wiki: launched ingest")
    assert agent2.launches[0]["objects"] == ["result:r1", "result:r2"]


def test_a_human_forced_run_is_never_held(substrate, agent2, monkeypatch):
    """Someone pressed the button and is watching. A hold there reads as the
    button being broken."""
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "4")
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_WAIT", "99999")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)

    line = wiki.beat(substrate, p, 100.0, agent2, forced_by="oleg")
    assert line.startswith("wiki: launched ingest")
    assert wiki_store.load_state(substrate, "p1")["run"]["forced_by"] == "oleg"


def test_a_batch_that_drains_without_running_forgets_it_was_armed(substrate, agent2, monkeypatch):
    """A held object can leave the queue without a run — reconciled, or
    quarantined. If the arm outlived it, the NEXT object would inherit an already
    expired deadline and launch alone: the exact batch-of-one this prevents."""
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "4")
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_WAIT", "600")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)

    wiki.beat(substrate, p, 100.0, agent2)
    state = wiki_store.load_state(substrate, "p1")
    assert state["batch_armed_at"] == 100.0
    state["quarantined"] = ["result:r1"]
    wiki_store.save_state(substrate, "p1", state)

    assert wiki.beat(substrate, p, 200.0, agent2) == ""
    assert "batch_armed_at" not in wiki_store.load_state(substrate, "p1")

    _seed_result(substrate, "r2", "s2", "p1", 300.0)
    wiki.beat(substrate, p, 400.0, agent2)
    assert agent2.launches == []                       # armed afresh, not expired
    assert wiki_store.load_state(substrate, "p1")["batch_armed_at"] == 400.0


def test_a_due_lint_is_never_held(substrate, agent2, monkeypatch):
    """A lint run has no batch to fill — its input is the whole bundle."""
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "4")
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_WAIT", "99999")
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "1")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki_store.ensure_bundle(substrate, "p1")
    (wiki_store.bundle_dir(substrate, "p1") / "concepts").mkdir(exist_ok=True)
    (wiki_store.bundle_dir(substrate, "p1") / "concepts" / "c.md").write_text("---\ntype: Concept\n---\n# C\n")
    state = wiki_store.load_state(substrate, "p1")
    state["ingests_since_lint"] = 5
    wiki_store.save_state(substrate, "p1", state)

    assert wiki.beat(substrate, p, 100.0, agent2).startswith("wiki: launched lint")
