"""Home-grown heartbeat: a thin CLI loop around Worker.run_one_beat()."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from coscience.claude_executor import ClaudeAgent
from coscience.dispatcher import CycleReport, Dispatcher
from coscience.loop_status import LoopStatus
from coscience.models import BeatOutcome, Program
from coscience.pm_claude import ClaudeCodeReasoner
from coscience.pm_runner import pm_run_once
from coscience.resources import load_pool
from coscience.scheduler import SchedulerPolicy
from coscience.substrate import Substrate
from coscience.worker import Worker


def run_once(repo_root: Path) -> BeatOutcome:
    worker = Worker(Substrate(repo_root), ClaudeAgent())
    return worker.run_one_beat()


def _make_pm_reasoner():
    return ClaudeCodeReasoner()


def dispatch_once(repo_root: Path) -> CycleReport:
    disp = Dispatcher(
        Substrate(repo_root), ClaudeAgent(),
        load_pool(repo_root), SchedulerPolicy(),
    )
    return disp.run_one_cycle()


def _status_loop(status: LoopStatus, beat, interval: float, max_beats: int | None) -> int:
    """Run `beat` on an interval. A background heartbeat re-renders every 5s so the
    clock/uptime tick between beats; `beat` returns (last_line, counters, claude_calls)."""
    status.render()                       # show the block before the first (slow) beat
    status.start_heartbeat(5.0)
    n = 0
    try:
        while max_beats is None or n < max_beats:
            last, counters, claude_calls = beat()
            status.record(last, counters, claude_calls)  # state + re-render
            n += 1
            if max_beats is None or n < max_beats:
                time.sleep(interval)
    finally:
        status.stop()
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coscience")
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("worker", help="run the single-sprint heartbeat worker")
    w.add_argument("--repo", required=True, type=Path)
    wmode = w.add_mutually_exclusive_group()
    wmode.add_argument("--once", action="store_true")
    wmode.add_argument("--loop", action="store_true")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--max-beats", type=int, default=None)

    d = sub.add_parser("dispatch", help="run the multi-sprint scheduling dispatcher")
    d.add_argument("--repo", required=True, type=Path)
    dmode = d.add_mutually_exclusive_group()
    dmode.add_argument("--once", action="store_true")
    dmode.add_argument("--loop", action="store_true")
    d.add_argument("--interval", type=float, default=5.0)
    d.add_argument("--max-beats", type=int, default=None)

    pg = sub.add_parser("program", help="manage research programs")
    pgsub = pg.add_subparsers(dest="program_command", required=True)
    pgc = pgsub.add_parser("create", help="create a program")
    pgc.add_argument("--repo", required=True, type=Path)
    pgc.add_argument("--id", required=True)
    pgc.add_argument("--title", required=True)
    pgc.add_argument("--goals", required=True)

    pm = sub.add_parser("pm", help="run the PM agent: propose sprints for active programs")
    pm.add_argument("--repo", required=True, type=Path)
    pmmode = pm.add_mutually_exclusive_group()
    pmmode.add_argument("--once", action="store_true")
    pmmode.add_argument("--loop", action="store_true")
    pm.add_argument("--interval", type=float, default=5.0)
    pm.add_argument("--max-rounds", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command == "program":
        if args.program_command == "create":
            Substrate(args.repo).save_program(
                Program(id=args.id, title=args.title, goals=args.goals))
            print(args.id)
            return 0

    if args.command == "worker":
        if args.once or not args.loop:
            print(run_once(args.repo).value)
            return 0

        def _beat():
            outcome = run_once(args.repo)
            return outcome.value, {outcome.value: 1}, 0  # shell worker: no Claude
        _status_loop(LoopStatus("worker"), _beat, args.interval, args.max_beats)
        return 0

    if args.command == "dispatch":
        if args.once or not args.loop:
            r = dispatch_once(args.repo)
            print(f"granted={r.granted} preempted={r.preempted} beaten={r.beaten} "
                  f"completed={r.completed} waiting={r.waiting}", flush=True)
            return 0

        def _beat():
            r = dispatch_once(args.repo)
            # a beaten sprint launches/advances its agent -> a Claude run that cycle
            # waiting is a current snapshot (shown live), not a per-cycle event to sum
            return (f"granted {r.granted} · completed {r.completed} · waiting {r.waiting}",
                    {"granted": r.granted, "completed": r.completed, "preempted": r.preempted},
                    r.beaten)
        _status_loop(LoopStatus("dispatch", uses_claude=True),
                     _beat, args.interval, args.max_beats)
        return 0

    if args.command == "pm":
        substrate = Substrate(args.repo)
        reasoner = _make_pm_reasoner()
        if args.once or not args.loop:
            for summary in pm_run_once(substrate, reasoner):
                print(f"{summary['program']}: cycle={summary['cycle']} "
                      f"submitted={summary['submitted']}", flush=True)
            return 0

        def _beat():
            summaries = pm_run_once(substrate, reasoner)
            ids = [sid for s in summaries for sid in s["submitted"]]
            reasoned = sum(0 if s.get("skipped") else 1 for s in summaries)
            if ids:
                last = f"proposed {', '.join(ids)}"
            elif reasoned:
                last = "reasoned — no new proposals"
            else:
                last = "idle — no input changed"
            # reasoned == Claude calls this beat (skipped cycles don't call Claude)
            return last, {"proposed": len(ids)}, reasoned
        _status_loop(LoopStatus("PM", uses_claude=True), _beat,
                     args.interval, args.max_rounds)
        return 0

    parser.error("unknown command")  # raises SystemExit
