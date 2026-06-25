"""Home-grown heartbeat: a thin CLI loop around Worker.run_one_beat()."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from coscience.claude_executor import ClaudeCodeExecutor
from coscience.dispatcher import CycleReport, Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome, Program
from coscience.resources import load_pool
from coscience.scheduler import SchedulerPolicy
from coscience.substrate import Substrate
from coscience.worker import Worker


def run_once(repo_root: Path) -> BeatOutcome:
    worker = Worker(Substrate(repo_root), ShellStepExecutor())
    return worker.run_one_beat()


def _make_executor(name: str):
    if name == "claude":
        return ClaudeCodeExecutor()
    return ShellStepExecutor()


def dispatch_once(repo_root: Path, executor_name: str = "shell") -> CycleReport:
    disp = Dispatcher(
        Substrate(repo_root), _make_executor(executor_name),
        load_pool(repo_root), SchedulerPolicy(),
    )
    return disp.run_one_cycle()


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
    d.add_argument("--executor", choices=["shell", "claude"], default="shell")

    pg = sub.add_parser("program", help="manage research programs")
    pgsub = pg.add_subparsers(dest="program_command", required=True)
    pgc = pgsub.add_parser("create", help="create a program")
    pgc.add_argument("--repo", required=True, type=Path)
    pgc.add_argument("--id", required=True)
    pgc.add_argument("--title", required=True)
    pgc.add_argument("--goals", required=True)

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
        beats = 0
        while args.max_beats is None or beats < args.max_beats:
            print(run_once(args.repo).value, flush=True)
            beats += 1
            if args.max_beats is None or beats < args.max_beats:
                time.sleep(args.interval)
        return 0

    if args.command == "dispatch":
        def _one():
            r = dispatch_once(args.repo, args.executor)
            print(f"granted={r.granted} preempted={r.preempted} beaten={r.beaten} "
                  f"completed={r.completed} waiting={r.waiting}", flush=True)
        if args.once or not args.loop:
            _one()
            return 0
        beats = 0
        while args.max_beats is None or beats < args.max_beats:
            _one()
            beats += 1
            if args.max_beats is None or beats < args.max_beats:
                time.sleep(args.interval)
        return 0

    parser.error("unknown command")  # raises SystemExit
