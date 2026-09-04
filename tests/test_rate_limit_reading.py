"""The rate-limit reading Claude Code puts in its own event stream: parsed out of a
run we already capture, recorded, and read by the launch gate and the dashboard
instead of polling the usage API."""
import json
import time

import pytest

from coscience import agent_stream, usage_meter
from coscience.worker import _usage_ok_from_limits

EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": 1788550200,
        "rateLimitType": "five_hour",
        "overageStatus": "allowed",
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.13, "resetsAt": 1788550200},
            "seven_day": {"utilization": 0.57, "resetsAt": 1788588000},
        },
    },
}


@pytest.fixture
def limits_file(tmp_path):
    """The path conftest's autouse isolation points the reading at."""
    return tmp_path / "rate-limit.json"


def _stream(*events) -> str:
    return "\n".join([json.dumps({"type": "system", "subtype": "init"})]
                     + [json.dumps(e) for e in events]
                     + [json.dumps({"type": "result", "result": "done"})])


def test_parses_the_info_out_of_a_stream():
    assert agent_stream.parse_rate_limit(_stream(EVENT)) == EVENT["rate_limit_info"]


def test_keeps_the_last_event():
    later = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed",
                                                             "resetsAt": 999}}
    assert agent_stream.parse_rate_limit(_stream(EVENT, later))["resetsAt"] == 999


def test_none_when_the_stream_carries_no_event():
    assert agent_stream.parse_rate_limit(_stream()) is None


def test_utilization_is_a_fraction_and_reads_back_as_percent():
    # 0.57 in the stream means 57%, while the usage API says 57.0 for the same
    # thing. Both feed one dashboard field, so the scale must not slip.
    usage_meter.record_limits(EVENT["rate_limit_info"])
    got = usage_meter.read_limits()
    assert got["windows"]["5h"]["pct"] == 13
    assert got["windows"]["week"]["pct"] == 57
    assert got["status"] == "allowed"


def test_recording_none_writes_nothing(limits_file):
    usage_meter.record_limits(None)
    assert not limits_file.exists()
    assert usage_meter.read_limits() is None


def test_a_reading_older_than_its_window_is_not_served(limits_file):
    limits_file.write_text(json.dumps({"ts": time.time() - 8 * 3600,
                                       "info": EVENT["rate_limit_info"]}))
    assert usage_meter.read_limits() is None


def test_payload_without_windows_yields_status_only():
    usage_meter.record_limits({"status": "allowed", "resetsAt": 1788550200})
    got = usage_meter.read_limits()
    assert got["windows"] == {}
    assert got["status"] == "allowed"


def test_the_budget_panel_prefers_the_recorded_reading(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not shell out to the usage script")

    monkeypatch.setattr(usage_meter.subprocess, "run", boom)
    usage_meter.record_limits(EVENT["rate_limit_info"])
    assert usage_meter.read_budget() == {
        "windows": usage_meter.read_limits()["windows"], "live": True}


def test_the_gate_decides_from_the_recorded_reading():
    assert _usage_ok_from_limits(None) is None
    assert _usage_ok_from_limits({"windows": {}, "status": "allowed"}) is None
    assert _usage_ok_from_limits({"windows": {}, "status": "rejected"}) is False
    healthy = {"windows": {"5h": {"pct": 13}, "week": {"pct": 57}}, "status": "allowed"}
    assert _usage_ok_from_limits(healthy, threshold=90.0, weekly_threshold=99.0) is True
    spent = {"windows": {"5h": {"pct": 95}, "week": {"pct": 57}}, "status": "allowed"}
    assert _usage_ok_from_limits(spent, threshold=90.0, weekly_threshold=99.0) is False
    week_spent = {"windows": {"5h": {"pct": 3}, "week": {"pct": 99}}, "status": "allowed"}
    assert _usage_ok_from_limits(week_spent, threshold=90.0, weekly_threshold=99.0) is False


def test_a_stale_usage_script_cache_reports_nothing_rather_than_a_frozen_number(monkeypatch):
    # The bug this fixes: an 8h-old cached 100% displayed as the current reading.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 3600))
    monkeypatch.setattr(usage_meter.subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": (
                            f"5h: 100% (resets Fri 6:40) | week: 52% (resets Sat 6:00) "
                            f"[cached {stamp}]")})())
    assert usage_meter.read_budget() is None
