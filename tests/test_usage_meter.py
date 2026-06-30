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
             "cost": 0, "cost_day": 0, "tokens": 0}
    stats = usage_meter.run_stats(tmp_path)
    assert stats == {"pm": empty, "worker": empty}


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
