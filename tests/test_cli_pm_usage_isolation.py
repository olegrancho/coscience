"""Regression for a test-isolation gap: `coscience.cli` does
`from coscience.worker import claude_usage_ok`, which binds its OWN name in the
`coscience.cli` namespace, separate from `coscience.worker.claude_usage_ok`.
conftest's autouse `_permissive_usage` fixture only patched the latter, so any
test that runs the CLI's PM loop (like test_cli_pm.py::test_pm_loop_runs_max_rounds)
was silently shelling out to the *real* usage script — passing or failing
depending on the host's actual Claude usage at the moment the suite ran, not on
anything the test itself controlled.

This test pins that down deterministically: point COSCIENCE_USAGE_SCRIPT at a
stub reporting usage comfortably above AUTONOMOUS_THRESHOLD (80%) and assert the
PM loop still runs its rounds. If the autouse fixture doesn't reach the
`coscience.cli` binding, the loop shells out for real, sees 99% > 80%, and pauses
with "Claude usage exhausted" instead of reasoning — so this fails for the same
reason test_pm_loop_runs_max_rounds does on a host over 80%."""

import coscience.cli as cli
from coscience.models import Program
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience.substrate import Substrate


def _seed_program(tmp_path):
    Substrate(tmp_path).save_program(Program(id="p1", title="C", goals="cure"))


def test_pm_loop_runs_max_rounds_when_real_usage_is_over_threshold(tmp_path, monkeypatch):
    stub = tmp_path / "usage_stub.py"
    stub.write_text(
        "print('5h: 99% (resets Thu 12:30) | week: 40% (resets Sun 23:00) [live]')\n")
    monkeypatch.setenv("COSCIENCE_USAGE_SCRIPT", str(stub))

    _seed_program(tmp_path)
    outs = [PMCycleOutput(report="r1"), PMCycleOutput(report="r2")]
    monkeypatch.setattr(cli, "_make_pm_reasoner", lambda *a: FakeReasoner(list(outs)))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)  # no real sleeping

    rc = cli.main(["pm", "--repo", str(tmp_path), "--loop", "--max-rounds", "2"])

    assert rc == 0
    # event-driven: round 1 reasons (cycle -> 1); round 2 sees no change and skips.
    # If the fixture's isolation doesn't reach coscience.cli, the loop instead
    # pauses on the (fake, exhausted) real usage script and cycle stays 0.
    assert Substrate(tmp_path).load_pm_state("p1").cycle == 1
