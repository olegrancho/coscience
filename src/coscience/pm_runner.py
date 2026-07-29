"""Runner over the PM heartbeat: beat every active program. Reasoner is injected
(FakeReasoner in tests; the real ClaudeCodeReasoner is wired in Phase 2b)."""
from __future__ import annotations

import time

from coscience.models import ProgramStatus
from coscience.pm_agent import pm_beat


def pm_run_once(substrate, reasoner, usage_ok=None) -> list[dict]:
    summaries = []
    for program in substrate.iter_programs(status=ProgramStatus.ACTIVE):
        # One program's beat must not abort the pass: a raise here used to unwind to
        # the loop's handler, so every program sorted after the broken one never beat.
        try:
            summaries.append(pm_beat(substrate, program.id, reasoner, usage_ok=usage_ok))
        except Exception as exc:
            summaries.append({"program": program.id, "error": f"{exc}"[:200],
                              "submitted": [], "proposed": [], "skipped": True})
    return summaries


def pm_loop(substrate, reasoner, interval: float = 5.0, max_rounds: int | None = None,
            sleep=time.sleep, usage_ok=None) -> int:
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        pm_run_once(substrate, reasoner, usage_ok=usage_ok)
        rounds += 1
        if max_rounds is None or rounds < max_rounds:
            sleep(interval)
    return rounds
