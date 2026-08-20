import json

from coscience.agent_stream import parse_stream


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
