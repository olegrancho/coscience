"""Home-grown heartbeat: a thin CLI loop around Worker.run_one_beat()."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome
from coscience.substrate import Substrate
from coscience.worker import Worker


def run_once(repo_root: Path) -> BeatOutcome:
    worker = Worker(Substrate(repo_root), ShellStepExecutor())
    return worker.run_one_beat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coscience")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worker", help="run the heartbeat worker")
    w.add_argument("--repo", required=True, type=Path)
    mode = w.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--max-beats", type=int, default=None)

    args = parser.parse_args(argv)
    if args.command != "worker":
        parser.error("unknown command")

    if args.once or not args.loop:
        outcome = run_once(args.repo)
        print(outcome.value)
        return 0

    beats = 0
    while args.max_beats is None or beats < args.max_beats:
        outcome = run_once(args.repo)
        print(outcome.value, flush=True)
        beats += 1
        if args.max_beats is None or beats < args.max_beats:
            time.sleep(args.interval)
    return 0
