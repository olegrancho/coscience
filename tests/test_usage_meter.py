from coscience import usage_meter


def test_record_and_aggregate_runs(tmp_path):
    now = 1_000_000.0
    # two pm calls (one old), one recent worker call — patch time for determinism
    import coscience.usage_meter as um
    times = iter([now - 10, now - 4000, now - 30])  # pm recent, pm old, worker recent
    orig = um.time.time
    um.time.time = lambda: next(times)
    try:
        usage_meter.record_run(tmp_path, "pm", "p1")
        usage_meter.record_run(tmp_path, "pm", "p1")
        usage_meter.record_run(tmp_path, "worker", "p1-c0-x")
    finally:
        um.time.time = orig

    stats = usage_meter.run_stats(tmp_path, now=now)
    assert stats["pm"]["total"] == 2
    assert stats["pm"]["last_hour"] == 1          # the 4000s-old one is outside the hour
    assert stats["worker"]["total"] == 1
    assert stats["worker"]["last_hour"] == 1


def test_run_stats_empty(tmp_path):
    empty = {"total": 0, "last_hour": 0, "last_day": 0, "last": None,
             "cost": 0, "cost_day": 0, "tokens": 0, "failed": 0,
             "input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
             "thinking_tokens": 0}
    stats = usage_meter.run_stats(tmp_path)
    assert stats == {"pm": empty, "worker": empty}


# --- per-component token split -------------------------------------------------
# The sum alone can't be read as cost: a cache read bills at a tenth of fresh
# input and an output token at five times it.

_ENVELOPE = {"input_tokens": 2, "output_tokens": 5,
             "cache_creation_input_tokens": 8570, "cache_read_input_tokens": 8073,
             "output_tokens_details": {"thinking_tokens": 3}}


def test_token_breakdown_splits_and_sums():
    br = usage_meter.token_breakdown(_ENVELOPE)
    assert br["input_tokens"] == 2
    assert br["cache_read_input_tokens"] == 8073
    assert br["thinking_tokens"] == 3
    # `tokens` is the four components only — thinking is already inside output.
    assert br["tokens"] == 2 + 5 + 8570 + 8073


def test_token_breakdown_omits_absent_fields():
    """Absent must stay distinguishable from zero, or a pre-breakdown row reads
    as a run that genuinely used no cache."""
    assert usage_meter.token_breakdown({}) == {}
    assert usage_meter.token_breakdown(None) == {}
    br = usage_meter.token_breakdown({"input_tokens": 7})
    assert "cache_read_input_tokens" not in br and "thinking_tokens" not in br
    assert br["tokens"] == 7


def test_record_run_stores_the_split(tmp_path):
    br = usage_meter.token_breakdown(_ENVELOPE)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=br["tokens"], usage=br)

    row = usage_meter.load_runs(tmp_path)[0]
    assert row["cache_read_input_tokens"] == 8073
    assert row["output_tokens"] == 5
    assert row["tokens"] == 16650          # unchanged meaning, so old rows still aggregate


def test_run_stats_aggregates_components_and_tolerates_old_rows(tmp_path):
    br = usage_meter.token_breakdown(_ENVELOPE)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=br["tokens"], usage=br)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=999)   # pre-breakdown row

    pm = usage_meter.run_stats(tmp_path)["pm"]
    assert pm["tokens"] == 16650 + 999     # the old row still counts in the total
    assert pm["cache_read_input_tokens"] == 8073   # ...and contributes 0 to the split
    assert pm["output_tokens"] == 5


def test_run_stats_sums_cost_and_tokens(tmp_path):
    usage_meter.record_run(tmp_path, "worker", "sp1", cost=0.5, tokens=1000, model="claude-opus-4-8")
    usage_meter.record_run(tmp_path, "worker", "sp2", cost=0.25, tokens=400)
    w = usage_meter.run_stats(tmp_path, now=10**12)["worker"]
    assert w["total"] == 2
    assert w["cost"] == 0.75
    assert w["tokens"] == 1400


def test_record_run_is_best_effort(tmp_path):
    # bad lines in the log are skipped, not fatal
    p = usage_meter._runs_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"ts": 1, "kind": "pm"}\nnot json\n')
    assert usage_meter.run_stats(tmp_path, now=10**12)["pm"]["total"] == 1


def test_record_run_stores_prompt_bytes_and_failure(tmp_path):
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=100, prompt_bytes=4096)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=50, ok=False)

    rows = usage_meter.load_runs(tmp_path)
    assert rows[0]["prompt_bytes"] == 4096
    assert "ok" not in rows[0]          # success stays the absent default — old rows read as ok
    assert rows[1]["ok"] is False
    assert "prompt_bytes" not in rows[1]  # unknown values are omitted, never zero-filled


def test_run_stats_counts_failed_calls(tmp_path):
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=100)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=50, ok=False)

    stats = usage_meter.run_stats(tmp_path)
    assert stats["pm"]["total"] == 2
    assert stats["pm"]["failed"] == 1
    assert stats["worker"]["failed"] == 0
