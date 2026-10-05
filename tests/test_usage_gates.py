"""G1: where each kind of agent stops launching is set from Compute and read from one
substrate file on every check, so the loops obey a change with no restart."""
import pytest

import coscience.worker as worker_mod
from coscience import usage_gates
from coscience.service import Service


def test_defaults_are_the_lines_the_code_used_to_hard_wire(tmp_path):
    assert usage_gates.load(tmp_path) == {
        "pm": {"5h": 80.0, "week": 99.0}, "worker": {"5h": 90.0, "week": 99.0},
        "wiki": {"5h": 70.0, "week": 99.0}}


def test_a_saved_line_is_what_the_next_check_uses(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(worker_mod, "claude_usage_ok",
                        lambda t, weekly_threshold=None, **k: seen.append((t, weekly_threshold)) or True)
    worker_mod.gate_ok(tmp_path, "pm")
    usage_gates.save(tmp_path, {"pm": {"5h": 50, "week": 95}})
    worker_mod.gate_ok(tmp_path, "pm")
    worker_mod.gate_ok(tmp_path, "wiki")
    assert seen == [(80.0, 99.0), (50.0, 95.0), (70.0, 99.0)]


def test_an_unreadable_file_falls_back_to_the_defaults(tmp_path):
    (tmp_path / ".coscience").mkdir()
    (tmp_path / ".coscience" / "usage-gates.json").write_text("{not json")
    assert usage_gates.limits(tmp_path, "worker") == (90.0, 99.0)
    (tmp_path / ".coscience" / "usage-gates.json").write_text('{"worker": {"5h": 400}}')
    assert usage_gates.limits(tmp_path, "worker") == (90.0, 99.0)


def test_save_refuses_a_value_outside_1_to_100(tmp_path):
    with pytest.raises(ValueError, match="worker 5h"):
        usage_gates.save(tmp_path, {"worker": {"5h": 0}})


def test_the_dashboard_reads_and_sets_them(tmp_path):
    svc = Service(tmp_path)
    assert svc.usage_stats()["gates"]["wiki"]["5h"] == 70.0
    svc.set_usage_gates({"wiki": {"5h": 60, "week": 90}})
    assert svc.usage_stats()["gates"]["wiki"] == {"5h": 60.0, "week": 90.0}


def test_a_window_grant_ends_at_the_planners_own_line(tmp_path):
    from coscience import grant
    usage_gates.save(tmp_path, {"pm": {"5h": 60, "week": 99}})
    g = {"limit": "window5h", "until": 10**12, "approved": []}
    windows = {"5h": {"pct": 65}}
    assert "60%" in grant.end_reason(g, 0, windows, repo_root=tmp_path)
