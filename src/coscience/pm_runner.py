"""Runner over the PM heartbeat: beat every active program. Reasoner is injected
(FakeReasoner in tests; the real ClaudeCodeReasoner is wired in Phase 2b)."""
from __future__ import annotations

import time

from coscience.models import ProgramStatus
from coscience.pm_agent import pm_beat


def pm_run_once(substrate, reasoner) -> list[dict]:
    summaries = []
    for program in substrate.iter_programs(status=ProgramStatus.ACTIVE):
        summaries.append(pm_beat(substrate, program.id, reasoner))
    return summaries


def pm_loop(substrate, reasoner, interval: float = 5.0, max_rounds: int | None = None,
            sleep=time.sleep) -> int:
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        pm_run_once(substrate, reasoner)
        rounds += 1
        if max_rounds is None or rounds < max_rounds:
            sleep(interval)
    return rounds
