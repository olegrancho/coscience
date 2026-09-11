import json

from coscience.agent_stream import parse_rate_limit, parse_rate_limits, parse_stream


def _line(**kw) -> str:
    return json.dumps(kw)


def test_parse_stream_returns_last_result_event():
    raw = "\n".join([
        _line(type="system", session_id="s-1"),
        _line(type="result", result="first", session_id="s-1"),
        _line(type="result", result="second", session_id="s-1",
              usage={"input_tokens": 10, "output_tokens": 4},
              total_cost_usd=0.02, num_turns=3, duration_ms=1500),
    ])
    got = parse_stream(raw)
    assert got is not None
    assert got.text == "second"
    assert got.session_id == "s-1"
    assert got.cost == 0.02
    assert got.turns == 3
    assert got.duration_ms == 1500
    assert got.usage == {"input_tokens": 10, "output_tokens": 4}


def test_parse_stream_none_when_no_result_event():
    assert parse_stream(_line(type="system", session_id="s-1")) is None


def test_parse_stream_ignores_non_json_noise():
    raw = "Claude usage limit reached\n" + _line(type="result", result="ok")
    assert parse_stream(raw).text == "ok"


def test_parse_stream_require_text_false_accepts_bare_result():
    raw = _line(type="result", session_id="s-9", subtype="error_during_execution")
    assert parse_stream(raw, require_text=True) is None
    got = parse_stream(raw, require_text=False)
    assert got is not None
    assert got.text == ""
    assert got.session_id == "s-9"


def _rl(util: float) -> str:
    return _line(type="rate_limit_event",
                 rate_limit_info={"status": "allowed",
                                  "unifiedWindows": {"five_hour": {"utilization": util,
                                                                   "resetsAt": 1789030200}}})


def test_the_rate_limit_span_brackets_the_run():
    """A run's own stream answers "where was the budget when this started".

    The launch stamp could not: it goes through the usage script, which needs a
    live OAuth token and serves a stale cache when the box has been idle — the
    exact case a launch is. Claude reports the window in the stream itself within
    the first few events, before the run has spent anything worth counting, so
    the first reading is the honest `before` and needs no credentials at all."""
    raw = "\n".join([
        _line(type="system", session_id="s-1"),
        _rl(0.0),
        _line(type="assistant"),
        _rl(0.03),
        _rl(0.24),
        _line(type="result", result="done"),
    ])
    first, last = parse_rate_limits(raw)
    assert first["unifiedWindows"]["five_hour"]["utilization"] == 0.0
    assert last["unifiedWindows"]["five_hour"]["utilization"] == 0.24
    # The existing single-reading accessor keeps meaning "the last one".
    assert parse_rate_limit(raw) == last


def test_a_stream_with_one_reading_reports_it_as_both_ends():
    raw = "\n".join([_rl(0.4), _line(type="result", result="done")])
    first, last = parse_rate_limits(raw)
    assert first is last is not None


def test_a_stream_with_no_reading_reports_neither():
    assert parse_rate_limits(_line(type="result", result="done")) == (None, None)
