"""Free space per machine (B1). The platform's root filesystem filled on 2026-09-19:
both loops failed for forty minutes, the usage rail went blank because its reading is
written to the same disk, and nothing anywhere said the disk was full."""
import pytest

from coscience import disk, host_health


# --- the reading --------------------------------------------------------------------

def test_free_gb_reads_a_real_filesystem(tmp_path):
    free = disk.free_gb(tmp_path)
    assert free is not None and free > 0


def test_free_gb_on_a_path_that_is_not_there_is_unknown(tmp_path):
    assert disk.free_gb(tmp_path / "no" / "such" / "place") is None


# --- what a reading means -----------------------------------------------------------

@pytest.mark.parametrize("free,expected", [
    (100.0, ""), (2.1, ""), (2.0, ""),          # at the threshold is still fine
    (1.99, "low"), (0.6, "low"), (0.5, "low"),  # at the gate is still only a warning
    (0.49, "critical"), (0.0, "critical"),
])
def test_level_thresholds(free, expected):
    assert disk.level(free) == expected


def test_an_unknown_reading_never_gates():
    """The platform does not act on a measurement it does not have."""
    assert disk.level(None) == ""
    assert disk.describe(None) == ""


def test_describe_says_the_amount_in_a_unit_that_fits():
    assert "1.5 GB" in disk.describe(1.5)
    assert "300 MB" in disk.describe(300 / 1024)
    assert disk.describe(50.0) == ""            # nothing to say


def test_the_critical_line_says_work_stops():
    assert "no new work" in disk.describe(0.2)


# --- the remote reading, on the same round trip as the liveness check ----------------

def test_the_probe_asks_for_free_space_even_with_no_run_root():
    assert "FREE_KB" in host_health._list_command("")


def test_the_probe_asks_for_both_when_there_is_a_run_root():
    cmd = host_health._list_command("~/coscience-runs")
    assert "FREE_KB" in cmd and "coscience-runs" in cmd


def test_free_space_is_parsed_out_of_a_probe_answer():
    assert host_health._free_gb("FREE_KB 2097152\nrun-a\nrun-b") == pytest.approx(2.0)


def test_a_host_that_reports_no_free_space_line_is_unknown():
    assert host_health._free_gb("run-a\nrun-b") is None
    assert host_health._free_gb("FREE_KB notanumber") is None


def test_the_free_space_line_is_not_mistaken_for_a_run_directory():
    assert host_health._run_dirs("FREE_KB 2097152\nrun-a\nrun-b") == ["run-a", "run-b"]


# --- B2: a machine with almost no disk takes no new work ----------------------------

def test_a_remote_host_under_the_gate_is_closed(tmp_path):
    from coscience.dispatcher import low_disk_hosts
    from coscience.resources import Host, ResourcePool

    pool = ResourcePool(hosts=[Host("local", {"cpu": 8}), Host("far", {"cpu": 8}, ssh="u@h")])
    assert low_disk_hosts(pool, {"far": {"free_gb": 0.2}}, tmp_path) == {"far": "low on disk"}


def test_a_remote_host_merely_low_still_takes_work(tmp_path):
    """`low` is a warning, not a gate — the platform says so and changes nothing."""
    from coscience.dispatcher import low_disk_hosts
    from coscience.resources import Host, ResourcePool

    pool = ResourcePool(hosts=[Host("far", {"cpu": 8}, ssh="u@h")])
    assert low_disk_hosts(pool, {"far": {"free_gb": 1.5}}, tmp_path) == {}


def test_a_host_that_reported_nothing_is_never_gated(tmp_path):
    from coscience.dispatcher import low_disk_hosts
    from coscience.resources import Host, ResourcePool

    pool = ResourcePool(hosts=[Host("far", {"cpu": 8}, ssh="u@h")])
    assert low_disk_hosts(pool, {}, tmp_path) == {}
    assert low_disk_hosts(pool, {"far": {"free_gb": None}}, tmp_path) == {}


def test_this_machine_is_measured_not_reported(tmp_path, monkeypatch):
    """It is not health-checked, so nothing would ever report a reading for it."""
    from coscience import dispatcher as dsp
    from coscience.resources import Host, ResourcePool

    monkeypatch.setattr(dsp.disk, "free_gb", lambda _p: 0.1)
    pool = ResourcePool(hosts=[Host("local", {"cpu": 8})])
    assert dsp.low_disk_hosts(pool, {}, tmp_path) == {"local": "low on disk"}


def test_the_worker_will_not_launch_an_agent_with_no_disk(substrate, monkeypatch):
    from coscience import worker as wk
    from tests.conftest import FakeAgent

    w = wk.Worker(substrate, FakeAgent())
    monkeypatch.setattr(wk.disk, "free_gb", lambda _p: 0.1)
    assert w._disk_ok() is False
    monkeypatch.setattr(wk.disk, "free_gb", lambda _p: 1.5)      # low, but not the gate
    assert w._disk_ok() is True
    monkeypatch.setattr(wk.disk, "free_gb", lambda _p: None)     # unknown never gates
    assert w._disk_ok() is True
