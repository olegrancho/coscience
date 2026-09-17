"""O11: an agent's survey of a probed server, built on program chat's thread store,
launch, collection, gates and call log."""
import json
import subprocess
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient

from coscience import chat_agent, host_survey, pause
from coscience import worker as worker_mod
from coscience.dispatcher import Dispatcher
from coscience.http_api import build_app
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.service import NotFoundError, Service
from tests.conftest import FakeAgent
from tests.host_probe_fakes import FakeRunner


def _svc(tmp_path):
    return Service(tmp_path)


def _probed(tmp_path, name="gpu1", ssh="gpuhost.example.com", runner=None):
    svc = _svc(tmp_path)
    record = svc.probe_host(name=name, ssh=ssh, runner=runner or FakeRunner())
    return svc, record


def _probed_with_failed_check(tmp_path, name="gpu1"):
    """One failed check ("detached job survives"), for the accept_overrides tests."""
    return _probed(tmp_path, name=name, runner=FakeRunner({"alive": (1, "", "")}))


def _fake_launch(token="123:456"):
    calls = []

    def launch(**kw):
        calls.append(kw)
        return token

    launch.calls = calls
    return launch


def _finish_turn(tdir, text="survey done", session_id="sess-9", code="0"):
    (tdir / "turn.out").write_text(
        json.dumps({"type": "assistant"}) + "\n"
        + json.dumps({"type": "result", "result": text, "session_id": session_id}) + "\n")
    (tdir / "turn.exit").write_text(code)


# --- starting and continuing a survey ----------------------------------------

def test_starting_a_survey_launches_full_scope_with_the_brief(tmp_path):
    svc, record = _probed(tmp_path)
    launch = _fake_launch()

    result = svc.survey_host("gpu1", launch=launch)

    assert len(launch.calls) == 1
    kw = launch.calls[0]
    assert kw["scope"] == "full"
    assert kw["resume"] is False
    assert kw["workdir"] == str(svc.substrate.survey_thread_dir("gpu1"))
    assert host_survey.SURVEY_BRIEF in kw["prompt"]
    assert "gpuhost.example.com" in kw["prompt"]
    for c in record["checks"]:
        state = "ok" if c["ok"] else "FAILED"
        assert f"{c['name']}: {state}" in kw["prompt"]

    # C1: a real Claude session id is minted up front, exactly as create_chat does
    # — an empty session_id makes `--session-id ''`, which fails every real turn.
    assert UUID(kw["session_id"])

    thread = svc.substrate.load_survey_thread("gpu1")
    assert thread.pending is True
    assert thread.agent_call
    assert UUID(thread.session_id)

    from coscience import usage_meter
    (call,) = usage_meter.calls(tmp_path)
    assert call["kind"] == "survey"
    assert result["pending"] is True


def test_a_follow_up_after_a_collected_turn_resumes_with_the_stored_session(tmp_path):
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    _finish_turn(tdir, text="here is what I found", session_id="sess-9")
    svc.get_survey("gpu1")                    # collects the first turn

    launch2 = _fake_launch()
    svc.survey_host("gpu1", "tell me more", launch=launch2)

    kw = launch2.calls[0]
    assert kw["resume"] is True
    assert kw["session_id"] == "sess-9"
    assert kw["prompt"] == "tell me more"


def test_a_follow_up_while_pending_raises(tmp_path):
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())

    with pytest.raises(ValueError, match="still working on the previous message"):
        svc.survey_host("gpu1", "more", launch=_fake_launch())


def test_a_follow_up_with_no_message_is_refused(tmp_path):
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    _finish_turn(tdir)
    svc.get_survey("gpu1")

    with pytest.raises(ValueError, match="message is required"):
        svc.survey_host("gpu1", "", launch=_fake_launch())


def test_no_probe_record_raises_not_found(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(NotFoundError):
        svc.survey_host("ghost")


def test_a_failed_login_probe_refuses_the_survey(tmp_path):
    svc = _svc(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1",
                   runner=FakeRunner({"probe": (255, "", "Host key verification failed.")}))

    with pytest.raises(ValueError, match="could not log in"):
        svc.survey_host("gpu1")


def test_paused_refuses_and_never_launches(tmp_path, monkeypatch):
    svc, _ = _probed(tmp_path)
    pause.set_paused(tmp_path, True)

    def boom(**kw):
        raise AssertionError("must not launch while paused")
    monkeypatch.setattr(chat_agent, "launch_turn", boom)

    with pytest.raises(ValueError, match="^Paused") as exc:
        svc.survey_host("gpu1")
    assert str(exc.value).startswith("Paused")


def test_usage_exhausted_refuses_and_never_launches(tmp_path, monkeypatch):
    svc, _ = _probed(tmp_path)
    monkeypatch.setattr(worker_mod, "claude_usage_ok", lambda *a, **k: False)

    def boom(**kw):
        raise AssertionError("must not launch when usage is exhausted")
    monkeypatch.setattr(chat_agent, "launch_turn", boom)

    with pytest.raises(ValueError, match="Claude usage is exhausted"):
        svc.survey_host("gpu1")


# --- collection ----------------------------------------------------------------

def test_get_survey_collects_a_finished_turn_and_closes_the_call(tmp_path):
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    _finish_turn(tdir, text="survey done", session_id="sess-9")

    state = svc.get_survey("gpu1")

    assert state["pending"] is False
    assert state["messages"][-1]["role"] == "pm"
    assert state["messages"][-1]["text"] == "survey done"

    from coscience import usage_meter
    (call,) = usage_meter.calls(tmp_path)
    assert call["kind"] == "survey" and call["status"] == "ok"


def test_a_dead_token_with_no_exit_gives_the_interrupted_text(tmp_path):
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch(token="999999999:1"))  # never a real process

    state = svc.get_survey("gpu1")

    assert state["pending"] is False
    assert "stopped before replying" in state["messages"][-1]["text"]


def test_get_survey_with_no_thread_reports_not_started(tmp_path):
    svc = _svc(tmp_path)
    state = svc.get_survey("ghost")
    assert state == {"name": "ghost", "pending": False, "messages": [],
                     "proposal": None, "proposal_error": "", "started": False}


def test_a_dispatcher_cycle_collects_a_finished_survey_turn(tmp_path):
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    _finish_turn(tdir, text="cycle collected me", session_id="sess-3")

    Dispatcher(svc.substrate, FakeAgent(), ResourcePool({}), SchedulerPolicy()).run_one_cycle(now=0.0)

    thread = svc.substrate.load_survey_thread("gpu1")
    assert thread.pending is False
    assert thread.messages[-1]["text"] == "cycle collected me"
    from coscience import usage_meter
    (call,) = usage_meter.calls(tmp_path)
    assert call["kind"] == "survey" and call["status"] == "ok"


def test_program_chat_commit_messages_are_unchanged_by_the_collect_into_refactor(substrate):
    """The chat_agent refactor (collect_thread -> collect_into) must not change what
    lands in the substrate's git log for program chat."""
    from coscience.models import Program
    from coscience.service import Service as _Service
    _git_repo(substrate.repo_root)
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    svc = _Service(substrate.repo_root)
    thread = svc.create_chat("p1", title="t")
    # A dead token (never a real process) gives "... interrupted" on collection.
    svc.post_chat_message("p1", thread["id"], "hello", launch=lambda **kw: "999999999:1")
    tdir = substrate.chat_thread_dir("p1", thread["id"])
    svc.get_chat_thread("p1", thread["id"])
    log = subprocess.run(["git", "-C", str(substrate.repo_root), "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert f"program p1: chat {thread['id']} interrupted" in log

    # A finished turn gives "... reply".
    svc.post_chat_message("p1", thread["id"], "again", launch=lambda **kw: "999999999:2")
    _finish_turn(tdir, text="hi back")
    svc.get_chat_thread("p1", thread["id"])
    log = subprocess.run(["git", "-C", str(substrate.repo_root), "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert f"program p1: chat {thread['id']} reply" in log


# --- I1: overrides are tied to the probe the agent saw ----------------------

def test_a_reprobe_to_a_different_target_makes_the_old_override_stale(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path, name="gpu1")
    _run_survey_to_completion(svc, "gpu1")
    tdir = svc.substrate.survey_thread_dir("gpu1")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "old-box runs tmux"}]}))
    svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)   # works once

    # Re-probe the SAME name at a different target; same check fails there too.
    svc.probe_host(name="gpu1", ssh="new-box.example.com",
                   runner=FakeRunner({"alive": (1, "", "")}))

    with pytest.raises(ValueError, match="older probe"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_a_failed_launch_after_a_reprobe_leaves_the_old_overrides_stale(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path, name="gpu1")
    tdir = _run_survey_to_completion(svc, "gpu1")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "old-box runs tmux"}]}))
    svc.probe_host(name="gpu1", ssh="new-box.example.com",
                   runner=FakeRunner({"alive": (1, "", "")}))

    def failing_launch(**_):
        raise OSError("could not start claude")

    with pytest.raises(OSError):
        svc.survey_host("gpu1", "look again", launch=failing_launch)

    with pytest.raises(ValueError, match="older probe"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_get_survey_hides_a_stale_proposal(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path, name="gpu1")
    tdir = _run_survey_to_completion(svc, "gpu1")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "fine"}]}))
    assert svc.get_survey("gpu1")["proposal"] is not None

    svc.probe_host(name="gpu1", ssh="new-box.example.com",
                   runner=FakeRunner({"alive": (1, "", "")}))

    state = svc.get_survey("gpu1")
    assert state["proposal"] is None
    assert "older probe" in state["proposal_error"]


def test_accept_overrides_while_the_survey_is_pending_is_refused(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path, name="gpu1")
    _run_survey_to_completion(svc, "gpu1")
    tdir = svc.substrate.survey_thread_dir("gpu1")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "fine"}]}))
    svc.survey_host("gpu1", "look again", launch=_fake_launch())   # now pending again

    with pytest.raises(ValueError, match="still working"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_a_follow_up_after_a_reprobe_carries_the_new_checks(tmp_path):
    svc, _ = _probed(tmp_path, name="gpu1")           # clean probe, first turn
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    _finish_turn(tdir, text="all good", session_id="sess-1")
    svc.get_survey("gpu1")

    # Re-probe: now a check fails.
    svc.probe_host(name="gpu1", ssh="gpuhost.example.com",
                   runner=FakeRunner({"alive": (1, "", "")}))

    launch2 = _fake_launch()
    svc.survey_host("gpu1", "please recheck", launch=launch2)

    prompt = launch2.calls[0]["prompt"]
    assert "probed again" in prompt
    assert "detached job survives: FAILED" in prompt
    assert prompt.endswith("please recheck")


# --- I2: dot-only names never escape the survey directory -------------------

@pytest.mark.parametrize("name", [".", ".."])
def test_probe_host_refuses_a_dot_only_name(tmp_path, name):
    with pytest.raises(ValueError, match="host name"):
        Service(tmp_path).probe_host(name=name, ssh="gpu1", runner=FakeRunner())


@pytest.mark.parametrize("name", [".", ".."])
def test_survey_host_refuses_a_dot_only_name(tmp_path, name):
    with pytest.raises(ValueError, match="host name"):
        Service(tmp_path).survey_host(name)


@pytest.mark.parametrize("name", [".", ".."])
def test_get_survey_refuses_a_dot_only_name(tmp_path, name):
    with pytest.raises(ValueError, match="host name"):
        Service(tmp_path).get_survey(name)


# --- M1: only coscience's own files in the survey directory are tracked -----

def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)


def test_survey_directory_gitignore_untracks_everything_but_its_own_files(tmp_path):
    _git_repo(tmp_path)
    svc, _ = _probed(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    (tdir / "turn.out").write_text("streaming output the agent's turn produced")
    (tdir / "script.sh").write_text("#!/bin/sh\necho hi\n")
    (tdir / "proposal.json").write_text(json.dumps({"capacity": {}, "gpus": [],
                                                     "notes": "", "overrides": []}))

    svc.substrate.commit("test commit")

    tracked = set(subprocess.run(["git", "-C", str(tmp_path), "ls-files"],
                                 capture_output=True, text=True).stdout.splitlines())
    rel = tdir.relative_to(tmp_path)
    assert str(rel / "turn.out") not in tracked
    assert str(rel / "script.sh") not in tracked
    assert str(rel / "thread.md") in tracked
    assert str(rel / ".gitignore") in tracked
    assert str(rel / "proposal.json") in tracked


# --- M6: a stale proposal.json with no probe record left ---------------------

def test_get_survey_with_probe_gone_but_a_leftover_proposal(tmp_path):
    svc, _ = _probed(tmp_path, name="gpu1")
    svc.survey_host("gpu1", launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir("gpu1")
    _finish_turn(tdir)
    svc.get_survey("gpu1")
    (tdir / "proposal.json").write_text(json.dumps({"capacity": {}, "gpus": [],
                                                     "notes": "", "overrides": []}))
    (tmp_path / ".coscience" / "host-probes" / "gpu1.json").unlink()   # probe record removed

    state = svc.get_survey("gpu1")

    assert state["proposal"] is None
    assert "probe record for gpu1 is gone" in state["proposal_error"]


# --- read_proposal ---------------------------------------------------------

def _record(checks):
    return {"checks": checks}


def test_read_proposal_no_file(tmp_path):
    assert host_survey.read_proposal(tmp_path, _record([])) == (None, "")


def test_read_proposal_valid_has_float_vram(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps({
        "capacity": {"cpu": 8, "memory_gb": 32},
        "gpus": [{"model": "NVIDIA GeForce RTX 2080 Ti", "vram_gb": 11}],
        "notes": "shared box",
        "overrides": [],
    }))
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert err == ""
    assert proposal["capacity"] == {"cpu": 8.0, "memory_gb": 32.0}
    assert proposal["gpus"] == [{"model": "NVIDIA GeForce RTX 2080 Ti", "vram_gb": 11.0}]
    assert isinstance(proposal["gpus"][0]["vram_gb"], float)


def test_read_proposal_non_json(tmp_path):
    (tmp_path / "proposal.json").write_text("{not json")
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "not valid JSON" in err


def test_read_proposal_negative_cpu(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps({"capacity": {"cpu": -1}}))
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "capacity.cpu" in err


def test_read_proposal_gpu_missing_vram(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps({"gpus": [{"model": "x"}]}))
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "vram_gb" in err


def test_read_proposal_override_names_a_check_that_did_not_fail(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps(
        {"overrides": [{"check": "run root writable", "reason": "fine"}]}))
    proposal, err = host_survey.read_proposal(
        tmp_path, _record([{"name": "some other check", "ok": False}]))
    assert proposal is None
    assert "run root writable" in err


def test_read_proposal_override_with_empty_reason(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps(
        {"overrides": [{"check": "run root writable", "reason": "   "}]}))
    proposal, err = host_survey.read_proposal(
        tmp_path, _record([{"name": "run root writable", "ok": False}]))
    assert proposal is None
    assert "run root writable" in err


# --- I3: a wrong-typed proposal.json is a proposal_error, never a 500 -------

def test_read_proposal_capacity_as_a_list_is_a_proposal_error(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps({"capacity": [1]}))
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "capacity" in err


def test_read_proposal_overrides_as_a_dict_is_a_proposal_error(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps({"overrides": {"a": "b"}}))
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "overrides" in err


def test_read_proposal_override_as_a_bare_string_is_a_proposal_error(tmp_path):
    (tmp_path / "proposal.json").write_text(json.dumps({"overrides": ["a"]}))
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "overrides" in err


def test_read_proposal_nan_capacity_is_a_proposal_error(tmp_path):
    (tmp_path / "proposal.json").write_text('{"capacity": {"cpu": NaN}}')
    proposal, err = host_survey.read_proposal(tmp_path, _record([]))
    assert proposal is None
    assert "capacity.cpu" in err


# --- confirm_host accept_overrides ------------------------------------------

def _run_survey_to_completion(svc, name, text="noted", message="survey it"):
    """Start and finish a survey turn (writes `probe-ref.json`), collect it, and
    return its thread directory — where a test then hand-writes `proposal.json`,
    exactly as the agent would have. `message` is non-empty by default so this
    also works as a follow-up on a thread an earlier call already created."""
    svc.survey_host(name, message, launch=_fake_launch())
    tdir = svc.substrate.survey_thread_dir(name)
    _finish_turn(tdir, text=text)
    svc.get_survey(name)
    return tdir


def test_confirm_host_with_failed_check_and_no_accept_is_refused(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path)
    with pytest.raises(ValueError, match="checks failed"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1})


def test_confirm_host_accept_overrides_with_no_survey_at_all_is_refused(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path)
    with pytest.raises(ValueError, match="no survey has run"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_confirm_host_accept_overrides_with_no_proposal_names_the_check(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path)
    _run_survey_to_completion(svc, "gpu1")

    with pytest.raises(ValueError, match="detached job survives"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_confirm_host_accept_overrides_missing_a_reason_names_the_check(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path)
    tdir = _run_survey_to_completion(svc, "gpu1")
    (tdir / "proposal.json").write_text(json.dumps({"overrides": []}))

    with pytest.raises(ValueError, match="detached job survives"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_confirm_host_accept_overrides_while_pending_is_refused(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path)
    svc.survey_host("gpu1", launch=_fake_launch())          # never finished: stays pending

    with pytest.raises(ValueError, match="still working"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)


def test_confirm_host_accept_overrides_writes_notes(tmp_path):
    svc, _ = _probed_with_failed_check(tmp_path)
    tdir = _run_survey_to_completion(svc, "gpu1")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives",
                       "reason": "known flaky on this box, harmless"}]}))

    svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True)

    entry = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["gpu1"]
    assert entry["notes"] == "Override detached job survives: known flaky on this box, harmless"


def test_confirm_host_notes_replace_declared_before_overrides_are_appended(tmp_path):
    """C2: a caller-given `notes` (the dashboard's Add form) replaces the probe's
    declared notes, and any override lines land after it."""
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpuhost.example.com", notes="declared note",
                   runner=FakeRunner({"alive": (1, "", "")}))
    tdir = _run_survey_to_completion(svc, "gpu1")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "harmless"}]}))

    svc.confirm_host(name="gpu1", capacity={"cpu": 1}, accept_overrides=True,
                     notes="the agent's own notes")

    entry = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["gpu1"]
    assert entry["notes"] == "the agent's own notes\nOverride detached job survives: harmless"


# --- update_host accept_overrides -------------------------------------------

def _pool_with_big(tmp_path):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(
        "cpu: 4\nhosts:\n  big:\n    ssh: big\n    run_root: ~/runs\n"
        "    capacity: {cpu: 8}\n")
    return Service(tmp_path)


def test_update_host_ssh_change_with_failed_check_follows_the_same_rule(tmp_path):
    svc = _pool_with_big(tmp_path)
    svc.probe_host(name="big", ssh="big2", run_root="~/runs",
                   runner=FakeRunner({"alive": (1, "", "")}))

    with pytest.raises(ValueError, match="checks failed"):
        svc.update_host("big", ssh="big2")
    with pytest.raises(ValueError, match="no survey has run"):
        svc.update_host("big", ssh="big2", accept_overrides=True)

    tdir = _run_survey_to_completion(svc, "big")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "known flaky"}]}))

    svc.update_host("big", ssh="big2", accept_overrides=True)

    entry = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]
    assert entry["ssh"] == "big2"
    assert entry["notes"] == "Override detached job survives: known flaky"


def test_m4_override_line_is_not_duplicated_across_separate_accepts(tmp_path):
    """A second target change that fails the same check, with the same reason,
    must not duplicate its Override line in notes."""
    svc = _pool_with_big(tmp_path)

    svc.probe_host(name="big", ssh="big", run_root="~/runs2",
                   runner=FakeRunner({"alive": (1, "", "")}))
    tdir = _run_survey_to_completion(svc, "big")
    (tdir / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "known flaky"}]}))
    svc.update_host("big", run_root="~/runs2", accept_overrides=True)
    entry = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]
    assert entry["notes"] == "Override detached job survives: known flaky"

    svc.probe_host(name="big", ssh="big", run_root="~/runs3",
                   runner=FakeRunner({"alive": (1, "", "")}))
    tdir2 = _run_survey_to_completion(svc, "big")
    (tdir2 / "proposal.json").write_text(json.dumps({
        "overrides": [{"check": "detached job survives", "reason": "known flaky"}]}))
    svc.update_host("big", run_root="~/runs3", accept_overrides=True)

    entry = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]
    assert entry["notes"] == "Override detached job survives: known flaky"


# --- routes ------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    c = TestClient(build_app(svc))
    c.svc = svc
    return c


def test_post_survey_route_starts_a_survey(client, monkeypatch):
    monkeypatch.setenv("COSCIENCE_ALLOW_ONBOARDING", "1")
    client.svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())
    monkeypatch.setattr(chat_agent, "launch_turn", lambda **kw: "123:456")

    r = client.post("/api/hosts/gpu1/survey", json={"message": ""})

    assert r.status_code == 200
    assert r.json()["pending"] is True


def test_get_survey_route(client, monkeypatch):
    monkeypatch.setenv("COSCIENCE_ALLOW_ONBOARDING", "1")
    client.svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())
    monkeypatch.setattr(chat_agent, "launch_turn", lambda **kw: "123:456")
    client.post("/api/hosts/gpu1/survey", json={"message": ""})

    r = client.get("/api/hosts/gpu1/survey")

    assert r.status_code == 200
    assert r.json()["started"] is True


@pytest.mark.parametrize("encoded", ["%2E", "%2E%2E"])
def test_survey_routes_refuse_a_dot_only_name(client, monkeypatch, encoded):
    """I2: '.' and '..' would otherwise resolve inside .coscience/host-surveys/,
    not to a per-server directory."""
    monkeypatch.setenv("COSCIENCE_ALLOW_ONBOARDING", "1")
    called = []
    monkeypatch.setattr(chat_agent, "launch_turn", lambda **kw: called.append(kw) or "tok")

    r1 = client.post(f"/api/hosts/{encoded}/survey", json={"message": ""})
    r2 = client.get(f"/api/hosts/{encoded}/survey")

    assert r1.status_code == 422
    assert r2.status_code == 422
    assert called == []


def test_survey_routes_are_off_by_default(client, monkeypatch):
    """M7: the switch is what refuses the request, not a missing probe — probe
    first, so a 403 here can't be mistaken for a 404."""
    monkeypatch.delenv("COSCIENCE_ALLOW_ONBOARDING", raising=False)
    client.svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())    # bypasses the route
    called = []
    monkeypatch.setattr(chat_agent, "launch_turn", lambda **kw: called.append(kw) or "tok")

    r1 = client.post("/api/hosts/gpu1/survey", json={"message": ""})
    r2 = client.get("/api/hosts/gpu1/survey")

    assert r1.status_code == 403
    assert r2.status_code == 403
    assert called == []
