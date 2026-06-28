import io
import sys

from coscience.loop_status import LoopStatus, _fmt_dur


def test_fmt_dur():
    assert _fmt_dur(5) == "5s"
    assert _fmt_dur(65) == "1m05s"
    assert _fmt_dur(3725) == "1h02m"


def test_hour_summary_aggregates_and_evicts():
    t = [1000.0]
    s = LoopStatus("PM", usage_cmd=[], clock=lambda: t[0])
    s.record("a", {"proposed": 1, "cycles": 1})
    t[0] += 10
    s.record("b", {"proposed": 2, "cycles": 1})
    summary = s._hour_summary()
    assert "2 runs" in summary and "3 proposed" in summary
    # jump just past an hour -> both earlier events evicted, only newest remains
    t[0] += 3601
    s.record("c", {"cycles": 1})
    assert s._hour_summary().startswith("1 run ")


def test_claude_line_variants():
    shell = LoopStatus("dispatch", uses_claude=False, usage_cmd=[], clock=lambda: 0)
    assert shell._claude_line() == "claude: not used (shell executor)"
    pm = LoopStatus("PM", uses_claude=True, usage_cmd=[], clock=lambda: 0)
    pm.record("x", {})
    assert "in use" in pm._claude_line()  # no usage cmd -> call-count fallback


def test_render_tty_writes_four_lines():
    s = LoopStatus("PM", usage_cmd=[], clock=lambda: 0)
    s._tty = True
    s.record("did a thing", {"proposed": 1})
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        s.render()
    finally:
        sys.stdout = old
    text = buf.getvalue()
    assert text.count("\n") == 4
    assert "PM · iter 1" in text
    assert "last:   did a thing" in text
    assert "1h:" in text and "claude:" in text


def test_bar_fill_and_width():
    from coscience.loop_status import _bar
    assert _bar(0, 6, color=False) == "░░░░░░"
    assert _bar(100, 6, color=False) == "██████"
    assert _bar(50, 6, color=False).count("█") == 3


def test_format_usage_two_bars_and_fallback():
    import re
    from coscience.loop_status import format_usage
    raw = "5h: 22% (resets Sun 2:10) | week: 83% (resets Sun 23:00) [live]"
    out = format_usage(raw, color=False)
    assert "→" not in out                       # no arrows
    assert "5h" in out and "wk" in out and "week" not in out
    assert "22%" in out and "83%" in out
    assert "02:10" in out                        # 5h: time only, zero-padded
    assert re.search(r"\d{1,2} [A-Z][a-z]{2}", out)  # wk: a date like '28 Jun'
    assert format_usage("garbage", color=False) == "garbage"


def test_claude_run_counter_total_and_last_hour():
    from coscience.loop_status import LoopStatus
    t = [1000.0]
    s = LoopStatus("PM", uses_claude=True, usage_cmd=[], clock=lambda: t[0])
    s.record("reasoned", {"proposed": 1}, claude_calls=1)   # a real cycle
    s.record("idle", {}, claude_calls=0)                    # skipped cycle: no Claude
    s.record("reasoned", {}, claude_calls=1)
    assert s.claude_total == 2                               # session total
    assert "2 claude" in s._hour_summary()                  # last-hour count
    assert "claude 2" in s.lines()[0]                       # header shows the total
    # an old Claude call falls out of the 1h window but stays in the session total
    t[0] += 3601
    s.record("reasoned", {}, claude_calls=1)
    assert s.claude_total == 3
    assert "1 claude" in s._hour_summary()
