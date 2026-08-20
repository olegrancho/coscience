"""Launch and collect a detached wiki run. The only module here that starts a
process — everything else in the wiki subsystem is pure or plain file IO.

There is no detached-job protocol and no resume: a wiki run either finishes its
batch in one turn or it does not, and a batch that cannot finish in one turn is
too large. The fix is a smaller COSCIENCE_WIKI_BATCH, not more machinery."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from coscience import executor, wiki_prompts
from coscience.wiki_store import WikiObject

_LEFTOVERS = ("agent.out", "agent.exit", "report.json", "lint-report.md")


class WikiAgent:
    """The real agent. Tests inject a double with the same three methods."""

    def __init__(self, claude_bin: str = "claude") -> None:
        self.claude_bin = claude_bin

    def launch(self, *, kind: str, program, bundle: Path, run_dir: Path,
               objects: list[tuple[WikiObject, str]] | None = None,
               report: str = "", model: str = "") -> str:
        run_dir.mkdir(parents=True, exist_ok=True)
        for name in _LEFTOVERS:
            (run_dir / name).unlink(missing_ok=True)
        if kind == "lint":
            text = wiki_prompts.render_lint(program, bundle, report, run_dir)
        else:
            text = wiki_prompts.render_ingest(program, bundle, objects or [], run_dir)
        (run_dir / "instructions.md").write_text(text)
        bundle.mkdir(parents=True, exist_ok=True)
        return executor.launch_detached(
            self._invocation(kind, run_dir, model), cwd=bundle)

    def _invocation(self, kind: str, run_dir: Path, model: str) -> str:
        """Mirrors ClaudeAgent._invocation, including the two flags that keep a
        run from outliving its turn."""
        out, exitf = run_dir / "agent.out", run_dir / "agent.exit"
        model_flag = f"--model {shlex.quote(model)} " if model else ""
        return (f"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 {self.claude_bin} -p "
                f"{shlex.quote(wiki_prompts.kickoff(kind, run_dir))} {model_flag}"
                f"--disallowedTools Monitor "
                f"--dangerously-skip-permissions --output-format stream-json --verbose "
                f"> {shlex.quote(str(out))} 2>&1; echo $? > {shlex.quote(str(exitf))}")

    def is_running(self, token: str) -> bool:
        return bool(token) and executor.is_running(token)

    def collect(self, run_dir: Path) -> tuple[str, dict]:
        """('running' | 'ok' | 'failed', report). A wiki run's completion is not a
        judgement call the way a sprint's is: exit 0 means done."""
        exitf = run_dir / "agent.exit"
        if not exitf.exists():
            return "running", {}
        try:
            code = int((exitf.read_text().strip() or "1"))
        except (ValueError, OSError):
            code = 1
        if code != 0:
            return "failed", {}
        return "ok", read_report(run_dir)


def read_report(run_dir: Path) -> dict:
    """report.json, or {} — a missing or corrupt report never fails a run that
    exited 0. Lint will find anything actually wrong with the pages."""
    try:
        data = json.loads((run_dir / "report.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_lint_report(run_dir: Path) -> str:
    try:
        return (run_dir / "lint-report.md").read_text()
    except OSError:
        return ""
