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


def read_outcome(run_dir: Path) -> dict:
    """What the run's own stream says it cost and how it ended.

    A sprint gets this from a cost sidecar the executor writes; a wiki run has no
    sidecar, so the numbers live in the final `result` envelope of `agent.out`.
    Reading them is what puts wiki spend in the call log at all — before this the
    wiki was the platform's busiest Claude consumer and reported nothing.

    Returns {} when there is no parseable result. Keys are those `finish_call`
    takes: status, cost, tokens, usage, turns, model, limits, limits_before."""
    from coscience import agent_stream, usage_meter
    try:
        raw = (run_dir / "agent.out").read_text()
    except OSError:
        return {}

    envelope = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)          # a torn final line simply never parses
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            envelope = ev                  # last complete result wins
    if envelope is None:
        return {}

    out: dict = {}
    if envelope.get("total_cost_usd") is not None:
        try:
            out["cost"] = float(envelope["total_cost_usd"])
        except (TypeError, ValueError):
            pass
    if envelope.get("num_turns") is not None:
        try:
            out["turns"] = int(envelope["num_turns"])
        except (TypeError, ValueError):
            pass
    usage = usage_meter.token_breakdown(envelope.get("usage"))
    if usage:
        out["usage"] = usage
        out["tokens"] = usage.get("tokens")
    models = envelope.get("modelUsage")
    if isinstance(models, dict) and models:
        out["model"] = next(iter(models))
    # 429 is not a plain failure: the agent ran, spent real money and exited with a
    # full envelope. B1 turns on telling the two apart, so the log must too.
    if envelope.get("is_error"):
        out["status"] = ("rate-limited" if envelope.get("api_error_status") == 429
                         else "failed")
    opened, info = agent_stream.parse_rate_limits(raw)
    # Feed the host cache too, not just this row. Chat, worker and PM all do this;
    # the wiki did not, so the box's busiest Claude consumer refreshed nothing and
    # every budget check fell back to shelling out to usage.py (F3).
    usage_meter.record_limits(info)
    limits = usage_meter.five_hour_window(info)
    if limits:
        out["limits"] = limits
    # Where the window stood as the run opened. The launch stamp asks the usage
    # script, which needs an OAuth token that has expired by the time an idle box
    # launches anything; the stream's first reading costs nothing and cannot be
    # stale, so it is what fills the `5h before` column.
    before = usage_meter.five_hour_window(opened)
    if before:
        out["limits_before"] = before
    return out
