import datetime

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
