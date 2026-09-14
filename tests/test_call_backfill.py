"""F5: rebuild call rows from wiki run dirs and worker sidecars, once."""
import json
import os

from coscience import call_backfill, usage_meter
from coscience.models import Program, Sprint, SprintStatus


def _wiki_run(substrate, pid, run_id, end, cost, kind="ingest"):
    run_dir = substrate.repo_root / "programs" / pid / ".wiki" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "instructions.md").write_text(f"# Wiki {kind} run\n")
    (run_dir / "agent.out").write_text(json.dumps({
        "type": "result", "total_cost_usd": cost, "num_turns": 30, "duration_ms": 600_000,
        "modelUsage": {"claude-opus-4-6": {"costUSD": cost}}}) + "\n")
    (run_dir / "agent.exit").write_text("0\n")
    os.utime(run_dir / "agent.exit", (end, end))
    return run_dir


def test_backfill_adds_missing_runs_skips_logged_ones_and_is_idempotent(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    _wiki_run(substrate, "p1", "r0001", end=1_000_000.0, cost=1.5)
    _wiki_run(substrate, "p1", "r0002", end=2_000_000.0, cost=2.0, kind="lint")
    # r0002 was logged at the time; its call ended 30s off the marker's mtime.
    rid = usage_meter.start_call(substrate.repo_root, "wiki-lint", program="p1", now=1_999_400.0)
    usage_meter.finish_call(substrate.repo_root, rid, status="ok", now=2_000_030.0)

    substrate.save_sprint(Sprint(id="p1-c1", status=SprintStatus.DONE, goals="g", plan=["x"],
                                 program="p1", model="claude-sonnet-5"))
    sidecar = substrate.sprint_dir("p1-c1") / "agent.cost.json"
    sidecar.write_text(json.dumps({"cost": 3.25, "turns": 90, "duration_ms": 1_200_000}))
    os.utime(sidecar, (3_000_000.0, 3_000_000.0))

    dry = call_backfill.backfill(substrate.repo_root)
    assert [(r["kind"], r["source"].split("/")[-1]) for r in dry["added"]] == [
        ("wiki-ingest", "r0001"), ("worker", "agent.cost.json")]
    assert dry["skipped"] == 1
    assert len(usage_meter.calls(substrate.repo_root)) == 1          # a dry run writes nothing

    call_backfill.backfill(substrate.repo_root, apply=True)
    rows = {c["kind"]: c for c in usage_meter.calls(substrate.repo_root)}
    ingest, worker = rows["wiki-ingest"], rows["worker"]
    assert ingest["status"] == "ok" and ingest["cost"] == 1.5 and ingest["backfilled"] is True
    assert ingest["model"] == "claude-opus-4-6"
    assert ingest["duration"] == 600.0 and ingest["program"] == "p1"
    assert worker["sprint"] == "p1-c1" and worker["program"] == "p1"
    assert worker["status"] == "unknown" and worker["cost"] == 3.25

    again = call_backfill.backfill(substrate.repo_root, apply=True)
    assert again["added"] == [] and again["skipped"] == 3
    assert len(usage_meter.calls(substrate.repo_root)) == 3
