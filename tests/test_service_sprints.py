import pytest

from coscience.models import SprintStatus
from coscience.service import NotFoundError, Service


def test_rationale_and_program_surface(tmp_path):
    from coscience.models import Sprint, SprintStatus
    svc = Service(tmp_path)
    svc.substrate.save_sprint(Sprint(id="sp9", status=SprintStatus.PROPOSED, goals="g",
        plan=["true"], program="prog", rationale="because X",
        title="Short name", summary="One skimmable line."))
    row = svc.list_sprints()[0]
    assert row["rationale"] == "because X"
    assert row["program"] == "prog"
    assert row["title"] == "Short name"
    assert row["summary"] == "One skimmable line."
    detail = svc.get_sprint("sp9")
    assert detail["rationale"] == "because X"
    assert detail["title"] == "Short name"
    assert detail["summary"] == "One skimmable line."


def test_submit_then_list_and_get(tmp_path):
    svc = Service(tmp_path)
    sid = svc.submit_sprint(id="sp1", goals="cure", plan=["echo hi"],
                            priority=3, resources_required={"gpu": 1})
    assert sid == "sp1"
    rows = svc.list_sprints()
    assert rows == [{"id": "sp1", "status": "proposed", "title": "", "summary": "",
                     "goals": "cure", "program": None, "priority": 3, "steps": 1,
                     "results": [], "rationale": "", "resources_required": {"gpu": 1.0},
                     "started_at": None, "model": "", "activity": None}]
    detail = svc.get_sprint("sp1")
    assert detail["status"] == "proposed"
    assert detail["resources_required"] == {"gpu": 1.0}
    assert detail["plan"] == ["echo hi"]
    assert detail["agent_running"] is False
    assert detail["lease"] is None


def test_submit_rejects_empty_plan(tmp_path):
    with pytest.raises(ValueError):
        Service(tmp_path).submit_sprint(id="sp1", goals="g", plan=[])


def test_submit_rejects_duplicate_id(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["true"])
    with pytest.raises(ValueError):
        svc.submit_sprint(id="sp1", goals="g", plan=["true"])


def test_approve_changes_status_and_filters(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["true"])
    svc.approve_sprint("sp1")
    assert svc.get_sprint("sp1")["status"] == "approved"
    assert [r["id"] for r in svc.list_sprints(status="approved")] == ["sp1"]
    assert svc.list_sprints(status="proposed") == []


def test_get_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).get_sprint("nope")


def test_approve_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).approve_sprint("nope")


def test_reject_moves_proposed_to_canceled(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["true"])
    svc.reject_sprint("sp1")
    assert svc.get_sprint("sp1")["status"] == "canceled"


def test_reject_non_proposed_raises(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["true"])
    svc.approve_sprint("sp1")
    with pytest.raises(ValueError):
        svc.reject_sprint("sp1")


def test_reject_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).reject_sprint("nope")


def _executing(svc, sid):
    # Force a sprint into EXECUTING for guard tests (no scheduler needed).
    s = svc.substrate.load_sprint(sid)
    s.status = SprintStatus.EXECUTING
    svc.substrate.save_sprint(s)


def test_edit_proposed_all_fields(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="old", plan=["a"])
    svc.edit_sprint("sp1", goals="new", plan=["b"],
                    priority=5, resources_required={"gpu": 2}, preemptible=False)
    d = svc.get_sprint("sp1")
    assert d["goals"] == "new"
    assert d["plan"] == ["b"]
    assert d["priority"] == 5
    assert d["resources_required"] == {"gpu": 2.0}
    assert d["preemptible"] is False


def test_edit_partial_leaves_other_fields(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="keep", plan=["a"], priority=1)
    svc.edit_sprint("sp1", priority=9)
    d = svc.get_sprint("sp1")
    assert d["goals"] == "keep"
    assert d["priority"] == 9


def test_edit_goals_blocked_when_not_proposed(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    svc.approve_sprint("sp1")
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", goals="nope")


def test_edit_priority_allowed_when_executing(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    _executing(svc, "sp1")
    svc.edit_sprint("sp1", priority=7)
    assert svc.get_sprint("sp1")["priority"] == 7


def test_edit_blocked_when_done(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    s = svc.substrate.load_sprint("sp1")
    s.status = SprintStatus.DONE
    svc.substrate.save_sprint(s)
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", priority=3)


def test_edit_empty_plan_raises(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    with pytest.raises(ValueError):
        svc.edit_sprint("sp1", plan=[])


def test_edit_missing_raises_notfound(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).edit_sprint("nope", priority=1)


def test_list_sprint_files_orders_and_labels(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    d = svc.substrate.sprint_dir("sp1")
    (d / "instructions.md").write_text("do the thing")
    (d / "agent.out").write_text("line1\nline2")
    (d / "scratchpad.md").write_text("# notes\nworking")
    (d / "solver.py").write_text("print('hi')")
    (d / "progress.md").write_text("---\nagent_token: x\n---")  # plumbing, hidden
    files = svc.list_sprint_files("sp1")
    names = [f["name"] for f in files]
    # spec + plumbing excluded; known docs first in fixed order, artifacts last
    assert names == ["scratchpad.md", "agent.out", "instructions.md", "solver.py"]
    by = {f["name"]: f for f in files}
    assert by["scratchpad.md"]["kind"] == "scratchpad"
    assert by["agent.out"]["label"] == "Agent log"
    assert by["solver.py"]["kind"] == "artifact"
    assert by["scratchpad.md"]["content"] == "# notes\nworking"
    assert by["solver.py"]["binary"] is False


def test_list_sprint_files_flags_binary(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    (svc.substrate.sprint_dir("sp1") / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    blob = next(f for f in svc.list_sprint_files("sp1") if f["name"] == "blob.bin")
    assert blob["binary"] is True
    assert blob["content"] == ""


def test_list_sprint_files_missing_raises(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).list_sprint_files("nope")


def test_add_sprint_comment_any_status(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    s = svc.substrate.load_sprint("sp1")
    s.status = SprintStatus.DONE                       # commenting allowed even when done
    svc.substrate.save_sprint(s)
    c = svc.add_sprint_comment("sp1", "please double-check the boundary case")
    assert c["text"] == "please double-check the boundary case" and c["id"]
    assert svc.get_sprint("sp1")["comments"] == [c]


def test_sprint_comment_target_routing(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    w = svc.add_sprint_comment("sp1", "for agent", target="worker")
    p = svc.add_sprint_comment("sp1", "for planner", target="pm")
    assert w["target"] == "worker" and p["target"] == "pm"
    with pytest.raises(ValueError):
        svc.add_sprint_comment("sp1", "x", target="bogus")


def test_add_sprint_comment_rejects_empty(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    with pytest.raises(ValueError):
        svc.add_sprint_comment("sp1", "  ")


def test_add_sprint_comment_missing_raises(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).add_sprint_comment("nope", "x")


def test_sprint_model_round_trips_and_is_editable_while_executing(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="sp1", goals="g", plan=["a"])
    assert svc.get_sprint("sp1")["model"] == ""          # default: launcher's model
    svc.edit_sprint("sp1", model="claude-sonnet-4-6")
    assert svc.get_sprint("sp1")["model"] == "claude-sonnet-4-6"
    _executing(svc, "sp1")
    svc.edit_sprint("sp1", model="claude-opus-4-8")       # switchable while running
    assert svc.get_sprint("sp1")["model"] == "claude-opus-4-8"
    assert svc.list_sprints()[0]["model"] == "claude-opus-4-8"


def test_program_pm_model_round_trips(tmp_path):
    from coscience.models import Program, ProgramStatus
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    assert svc.get_program("p1")["pm_model"] == ""
    svc.set_program_model("p1", "claude-sonnet-4-6")
    assert svc.get_program("p1")["pm_model"] == "claude-sonnet-4-6"
    svc.set_program_model("p1", "")                       # clearing returns to default
    assert svc.get_program("p1")["pm_model"] == ""


def test_program_workdir_round_trips_and_flags_existence(tmp_path):
    from coscience.models import Program, ProgramStatus
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    assert svc.get_program("p1")["workdir"] == ""
    proj = tmp_path / "hobby"; proj.mkdir()
    out = svc.set_program_workdir("p1", str(proj))
    assert out["workdir"] == str(proj) and out["exists"] is True
    assert svc.get_program("p1")["workdir"] == str(proj)
    # a path that doesn't exist is stored but flagged, not rejected
    missing = svc.set_program_workdir("p1", "/no/such/dir")
    assert missing["workdir"] == "/no/such/dir" and missing["exists"] is False


def test_program_sprints_ordered_by_creation(tmp_path):
    svc = Service(tmp_path)
    from coscience.models import Program, ProgramStatus
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    # created in this order despite reverse-alphabetical ids
    for sid, ts in [("zzz", 100.0), ("mmm", 200.0), ("aaa", 300.0)]:
        svc.submit_sprint(id=sid, goals="g", plan=["a"], program="p1")
        s = svc.substrate.load_sprint(sid)
        s.created_at = ts
        svc.substrate.save_sprint(s)
    order = [s["id"] for s in svc.get_program("p1")["sprints"]]
    assert order == ["aaa", "mmm", "zzz"]  # newest first
