"""The Claude call log: one event at launch, one at collect, one row on read.

Every LLM call the platform makes should appear on Compute with what it cost and
how it ended. Two properties drive the design:

- A call is recorded in TWO events, because a row written only at the end cannot
  describe a process that was killed — and killed processes (the 08-30..09-01
  rate-limit deaths) are what the log most needs to show. The open state is
  INFERRED on read, never written: whoever would write a died-event is the
  process that died.
- The log is host-local and keyed per substrate. Avatar runs two substrates and a
  shared file would merge one program's spend into another's.
"""

from __future__ import annotations

import json

from coscience import usage_meter


def _events(repo_root):
    return [json.loads(l) for l in
            usage_meter.runs_path(repo_root).read_text().splitlines() if l.strip()]


# --- where the log lives (F6) ------------------------------------------------

def test_the_log_is_not_written_inside_the_substrate(tmp_path):
    usage_meter.start_call(tmp_path, "pm", program="p1")
    assert not (tmp_path / ".coscience" / "runs.jsonl").exists()
    assert usage_meter.runs_path(tmp_path).is_file()


def test_two_substrates_do_not_share_a_log(tmp_path):
    """Both real substrates are named `coscience`, so the key cannot be the
    basename — this is the collision the per-substrate key exists to prevent."""
    a, b = tmp_path / "one" / "coscience", tmp_path / "two" / "coscience"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    usage_meter.start_call(a, "pm", program="p1")
    usage_meter.start_call(b, "pm", program="p2")

    assert usage_meter.runs_path(a) != usage_meter.runs_path(b)
    assert len(usage_meter.calls(a)) == 1
    assert len(usage_meter.calls(b)) == 1


def test_rows_already_committed_in_the_substrate_are_still_read(tmp_path):
    """The old location becomes a frozen archive rather than something to migrate:
    nothing is moved, nothing can be lost, and `git rm --cached` stays optional."""
    legacy = tmp_path / ".coscience"
    legacy.mkdir(parents=True)
    (legacy / "runs.jsonl").write_text(
        json.dumps({"ts": 100.0, "kind": "pm", "ref": "p1", "cost": 1.5}) + "\n")

    usage_meter.start_call(tmp_path, "worker", sprint="p1-c0-x")
    got = usage_meter.calls(tmp_path)

    assert len(got) == 2
    assert sum(c.get("cost") or 0 for c in got) == 1.5


# --- folding two events into one row (F2) ------------------------------------

def test_a_finished_call_is_one_row(tmp_path):
    rid = usage_meter.start_call(tmp_path, "wiki-ingest", program="p3",
                                 model="claude-opus-4-6", now=10.0)
    usage_meter.finish_call(tmp_path, rid, status="ok", cost=2.5, now=25.0)

    assert len(_events(tmp_path)) == 2          # two events on disk
    (call,) = usage_meter.calls(tmp_path)       # one row in the UI
    assert call["kind"] == "wiki-ingest"
    assert call["program"] == "p3"
    assert call["model"] == "claude-opus-4-6"
    assert call["status"] == "ok"
    assert call["cost"] == 2.5
    assert call["started_at"] == 10.0 and call["ended_at"] == 25.0
    assert call["duration"] == 15.0


def test_program_and_sprint_are_separate_fields(tmp_path):
    """`ref` used to mean program for PM rows and sprint for worker rows, so a
    worker's program could not be read off a row at all."""
    rid = usage_meter.start_call(tmp_path, "worker", program="p2",
                                 sprint="p2-c35-ranklib", now=1.0)
    usage_meter.finish_call(tmp_path, rid, status="ok", now=2.0)

    (call,) = usage_meter.calls(tmp_path)
    assert call["program"] == "p2" and call["sprint"] == "p2-c35-ranklib"


def test_an_unfinished_call_is_running_inside_the_grace_window(tmp_path):
    usage_meter.start_call(tmp_path, "pm", program="p1", now=100.0)
    (call,) = usage_meter.calls(tmp_path, now=110.0, grace=60.0)
    assert call["status"] == "running" and call["ended_at"] is None


def test_an_unfinished_call_is_lost_once_the_grace_window_passes(tmp_path):
    """The killed-process case. Nothing wrote this status — it is inferred, which
    is the only way it can be right when the writer is the thing that died."""
    usage_meter.start_call(tmp_path, "wiki-ingest", program="p3", now=100.0)
    (call,) = usage_meter.calls(tmp_path, now=100_000.0, grace=60.0)
    assert call["status"] == "lost"
    assert call["ended_at"] is None and call["cost"] is None


def test_a_rate_limited_call_keeps_its_cost(tmp_path):
    """A 429 is not a kill: the agent exits with a full envelope, so these are
    `rate-limited` WITH cost, not `lost`. Most of the 08-30..09-01 spend was this."""
    rid = usage_meter.start_call(tmp_path, "wiki-ingest", program="p3", now=1.0)
    usage_meter.finish_call(tmp_path, rid, status="rate-limited", cost=2.25, now=9.0)

    (call,) = usage_meter.calls(tmp_path)
    assert call["status"] == "rate-limited" and call["cost"] == 2.25


def test_calls_are_newest_first(tmp_path):
    for i, kind in enumerate(("pm", "worker", "wiki-lint")):
        usage_meter.finish_call(
            tmp_path, usage_meter.start_call(tmp_path, kind, now=float(i)),
            status="ok", now=float(i) + 0.5)
    assert [c["kind"] for c in usage_meter.calls(tmp_path)] == ["wiki-lint", "worker", "pm"]


# --- legacy rows -------------------------------------------------------------

def test_a_legacy_row_folds_to_one_finished_call(tmp_path):
    """The 234 rows written before the split have no `ev` and no id. They were
    written at the end of a call, so they are complete on their own."""
    usage_meter.record_run(tmp_path, "pm", "p1", cost=0.5, model="claude-opus-5")
    (call,) = usage_meter.calls(tmp_path)
    assert call["kind"] == "pm" and call["cost"] == 0.5 and call["status"] == "ok"
    assert call["program"] == "p1"           # a pm `ref` is a program id


def test_a_legacy_worker_row_reads_its_ref_as_a_sprint(tmp_path):
    usage_meter.record_run(tmp_path, "worker", "p1-c0-x", cost=1.0)
    (call,) = usage_meter.calls(tmp_path)
    assert call["sprint"] == "p1-c0-x" and call["program"] == ""


def test_a_legacy_failed_row_reads_as_failed(tmp_path):
    usage_meter.record_run(tmp_path, "pm", "p1", ok=False)
    (call,) = usage_meter.calls(tmp_path)
    assert call["status"] == "failed"


# --- the window either side (F3 groundwork) ----------------------------------

def test_the_window_reading_is_carried_on_both_events(tmp_path):
    rid = usage_meter.start_call(tmp_path, "wiki-ingest", program="p3", now=1.0,
                                 limits={"pct": 61, "resets": "Fri 21:19"})
    usage_meter.finish_call(tmp_path, rid, status="rate-limited", now=9.0,
                            limits={"pct": 104, "resets": "Fri 21:19"})

    (call,) = usage_meter.calls(tmp_path)
    assert call["limits_before"]["pct"] == 61
    assert call["limits_after"]["pct"] == 104


# --- aggregates keep working -------------------------------------------------

def test_run_stats_still_aggregates_and_now_sees_the_new_kinds(tmp_path):
    usage_meter.finish_call(tmp_path,
                            usage_meter.start_call(tmp_path, "pm", now=1.0),
                            status="ok", cost=1.0, now=2.0)
    usage_meter.finish_call(tmp_path,
                            usage_meter.start_call(tmp_path, "wiki-ingest", now=3.0),
                            status="ok", cost=2.0, now=4.0)

    stats = usage_meter.run_stats(tmp_path, now=10.0)
    assert stats["pm"]["total"] == 1 and stats["pm"]["cost"] == 1.0
    assert stats["wiki-ingest"]["total"] == 1 and stats["wiki-ingest"]["cost"] == 2.0


def test_logging_never_raises(tmp_path):
    """Best-effort throughout: a call must never fail because its row would not
    write. An unwritable cache root is the realistic case."""
    import os
    os.environ["COSCIENCE_CACHE_DIR"] = "/proc/nonexistent/cannot-create"
    try:
        rid = usage_meter.start_call(tmp_path, "pm", program="p1")
        usage_meter.finish_call(tmp_path, rid, status="ok", cost=1.0)
        assert usage_meter.calls(tmp_path) == []
    finally:
        del os.environ["COSCIENCE_CACHE_DIR"]


# --- the endpoint the Compute log reads (F4) ---------------------------------

def test_recent_calls_are_newest_first_and_capped(tmp_path):
    for i in range(5):
        usage_meter.finish_call(
            tmp_path, usage_meter.start_call(tmp_path, "pm", program="p1", now=float(i)),
            status="ok", cost=1.0, now=float(i) + 0.5)
    got = usage_meter.recent_calls(tmp_path, limit=3)
    assert len(got) == 3
    assert got[0]["ended_at"] > got[-1]["ended_at"]


def test_recent_calls_carry_every_column_the_log_renders(tmp_path):
    rid = usage_meter.start_call(tmp_path, "wiki-ingest", program="p3",
                                 sprint="", model="claude-opus-4-6", now=1.0,
                                 limits={"pct": 61, "resets": "Fri 21:19"})
    usage_meter.finish_call(tmp_path, rid, status="rate-limited", cost=2.25,
                            turns=41, now=61.0,
                            limits={"pct": 104, "resets": "Fri 21:19"})
    (call,) = usage_meter.recent_calls(tmp_path, limit=10)
    for field in ("model", "kind", "program", "sprint", "started_at", "ended_at",
                  "status", "limits_before", "limits_after", "cost", "duration"):
        assert field in call, f"missing column: {field}"


def test_the_http_layer_serves_the_call_log(substrate):
    """F4's endpoint: the table reads this, so its shape is a contract."""
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app

    rid = usage_meter.start_call(substrate.repo_root, "wiki-ingest", program="p3",
                                 model="claude-opus-4-6", now=1.0)
    usage_meter.finish_call(substrate.repo_root, rid, status="rate-limited",
                            cost=2.25, now=61.0)

    from coscience.service import Service
    client = TestClient(build_app(Service(substrate.repo_root)))
    body = client.get("/api/usage/calls?limit=50").json()

    assert len(body["calls"]) == 1
    row = body["calls"][0]
    assert row["kind"] == "wiki-ingest" and row["status"] == "rate-limited"
    assert row["cost"] == 2.25 and row["duration"] == 60.0


def test_the_window_stamp_falls_back_when_the_recorded_reading_is_stale(monkeypatch, tmp_path):
    """`limits_before` was empty on 6 of 7 real wiki calls.

    `read_limits()` deliberately returns None past 15 minutes — right for the
    budget panel, where a number from a window that may have reset is worse than
    a blank. But a launch stamp then never lands, because launches happen long
    after the last call finished. `read_budget()` already has the correct
    behaviour: prefer the recorded reading, fall back to the usage script under
    its own 300s throttle."""
    monkeypatch.setenv("COSCIENCE_LIMITS_CACHE", str(tmp_path / "absent.json"))
    monkeypatch.setattr(usage_meter, "usage_output",
                        lambda *a, **k: "5h: 44% (resets Fri 21:19) | week: 10% (resets Sun 23:00) [live]")

    assert usage_meter.read_limits() is None          # nothing recorded at all
    got = usage_meter.current_window()
    assert got and got["pct"] == 44


def test_the_run_s_own_first_reading_beats_the_launch_stamp(tmp_path):
    """`limits_before` was empty on the 09-09 wiki ingest, and on every other call
    that opened after an idle stretch.

    The launch stamp goes through the usage script, which needs a live OAuth token;
    on a box idle since the previous run that token has expired, the script serves
    its own stale cache, and `read_budget` correctly refuses to report a number
    from a window that may have reset. So the stamp lands only when something
    warmed the cache in the last 15 minutes — the opposite of a launch. The run's
    own stream carries the reading a few events in, needs no credentials, and
    cannot be stale, so it wins wherever both exist."""
    rid = usage_meter.start_call(tmp_path, "wiki-ingest", now=100.0)   # cold: no stamp
    usage_meter.finish_call(tmp_path, rid, now=200.0, cost=1.46,
                            limits_before={"pct": 0, "resets": "Thu 1:50"},
                            limits={"pct": 24, "resets": "Thu 1:50"})

    (call,) = usage_meter.calls(tmp_path, now=300.0)
    assert call["limits_before"] == {"pct": 0, "resets": "Thu 1:50"}
    assert call["limits_after"] == {"pct": 24, "resets": "Thu 1:50"}
    # It is a reading, not a stray field copied onto the row.
    assert "limits_before" not in _events(tmp_path)[0]


def test_a_launch_stamp_survives_a_run_that_reports_no_reading(tmp_path):
    """A warm launch still stamps. An end event with nothing to say about the
    window must not blank what the launch knew."""
    rid = usage_meter.start_call(tmp_path, "pm", now=100.0,
                                 limits={"pct": 9, "resets": "Mon 17:10"})
    usage_meter.finish_call(tmp_path, rid, now=200.0, cost=0.74)

    (call,) = usage_meter.calls(tmp_path, now=300.0)
    assert call["limits_before"] == {"pct": 9, "resets": "Mon 17:10"}


# --- F8: a live process is running whatever its age -------------------------

def test_an_unfinished_call_whose_process_is_alive_stays_running_past_the_grace(tmp_path):
    """Worker runs of 35-67 min showed `lost` while healthy, and dropped out of the
    rail's live-agent count, because age alone decided."""
    import os
    from coscience.executor import process_token
    usage_meter.start_call(tmp_path, "worker", sprint="p2-c28", now=100.0,
                           token=process_token(os.getpid()))
    (call,) = usage_meter.calls(tmp_path, now=100_000.0, grace=60.0)
    assert call["status"] == "running"


def test_a_dead_process_is_lost_at_once_not_after_the_grace(tmp_path):
    """F10: an agent that died at minute 2 read `running` until minute 15."""
    usage_meter.start_call(tmp_path, "worker", now=100.0, token="999999999:1")
    assert usage_meter.calls(tmp_path, now=110.0, grace=60.0)[0]["status"] == "lost"


def test_a_token_that_is_not_a_process_falls_back_to_age(tmp_path):
    usage_meter.start_call(tmp_path, "worker", now=100.0, token="fake:1")
    (call,) = usage_meter.calls(tmp_path, now=100_000.0, grace=60.0)
    assert call["status"] == "lost"
    assert "token" not in call
