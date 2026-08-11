import datetime
from types import SimpleNamespace

from coscience.worker import _usage_ok_from_output

NOW = datetime.datetime(2026, 7, 2, 14, 40, 0, tzinfo=datetime.timezone.utc)


def _line(five, week, tag):
    return f"5h: {five}% (resets Thu 12:30) | week: {week}% (resets Sun 23:00) [{tag}]"


def test_live_exhausted_pauses():
    assert _usage_ok_from_output(_line(100, 27, "live"), now=NOW) is False


def test_live_healthy_allows():
    assert _usage_ok_from_output(_line(23, 27, "live"), now=NOW) is True


def test_fresh_cache_exhausted_still_pauses():
    stamp = (NOW - datetime.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _usage_ok_from_output(_line(100, 27, f"cached {stamp}"), now=NOW) is False


def test_stale_cache_exhausted_fails_open():
    # The window may have reset since this reading — don't pin the pause forever.
    stamp = (NOW - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _usage_ok_from_output(_line(100, 27, f"cached {stamp}"), now=NOW) is True


def test_unparseable_cache_stamp_fails_open():
    assert _usage_ok_from_output(_line(100, 27, "cached ???"), now=NOW) is True


def test_usage_script_path_honours_the_env_var(monkeypatch):
    from coscience import usage_meter
    monkeypatch.delenv("COSCIENCE_USAGE_SCRIPT", raising=False)
    assert usage_meter.usage_script_path().endswith(".claude/skills/usage/usage.py")
    monkeypatch.setenv("COSCIENCE_USAGE_SCRIPT", "/opt/usage.py")
    assert usage_meter.usage_script_path() == "/opt/usage.py"


def test_the_gate_reads_the_configured_script(monkeypatch, tmp_path):
    from coscience import worker as worker_mod
    monkeypatch.undo()          # drop conftest's autouse stub — see the fail-closed test
    fake = tmp_path / "usage.py"
    monkeypatch.setenv("COSCIENCE_USAGE_SCRIPT", str(fake))

    # A real host may have the actual usage skill installed and healthy, in which
    # case claude_usage_ok() would return True regardless of whether the configured
    # path was honoured (and it fails OPEN on error, so a broken wiring can't even
    # be caught by forcing an error). Assert on the argv the gate actually shelled
    # out with, not just the boolean, so a regression to the hardcoded default is
    # caught on every host, real skill or not.
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout="5h: 3% (resets Thu 12:30) [live]")

    monkeypatch.setattr(worker_mod.subprocess, "run", fake_run)
    result = worker_mod.claude_usage_ok()

    assert calls, "claude_usage_ok() never called subprocess.run"
    assert calls[0][-1] == str(fake)     # honoured the configured path, not the default
    assert result is True                # ... and read the controlled stdout correctly


def test_autonomous_threshold_reserves_headroom():
    """At 85% used, a human-triggered call still goes through but an autonomous
    loop stands down, leaving the rest of the window for the human."""
    from coscience.worker import AUTONOMOUS_THRESHOLD, WORKER_THRESHOLD
    out = "5h: 85% (resets Thu 12:30) | week: 40% (resets Sun 23:00) [live]"
    assert _usage_ok_from_output(out) is True                              # human: 100
    assert _usage_ok_from_output(out, threshold=WORKER_THRESHOLD) is True  # worker: 90
    assert _usage_ok_from_output(out, threshold=AUTONOMOUS_THRESHOLD) is False  # PM loop: 80


def test_gate_can_fail_closed(monkeypatch):
    from coscience import worker as worker_mod
    # conftest's autouse `_permissive_usage` fixture replaces
    # worker_mod.claude_usage_ok with a lambda that always returns True. The
    # `monkeypatch` fixture is function-scoped and SHARED with that fixture, so
    # undo() restores the real function — without this the assertions below would
    # be testing the stub.
    monkeypatch.undo()

    def _boom(*a, **k):
        raise OSError("no such script")
    monkeypatch.setattr(worker_mod.subprocess, "run", _boom)
    assert worker_mod.claude_usage_ok() is True                    # default: fail open
    assert worker_mod.claude_usage_ok(fail_open=False) is False    # loops: fail closed
