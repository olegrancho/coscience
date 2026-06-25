# Co-Science Platform — Phase 2b (PM Real Brain) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the PM machinery (built in Phase 2a against `FakeReasoner`) a real LLM brain — a `ClaudeCodeReasoner` that renders a prompt from `PMContext`, runs a headless Claude Code session, and parses the structured JSON back into a `PMCycleOutput` — plus the `coscience pm` CLI that wires it into the runner, plus a manual acceptance runbook that proves the whole loop end-to-end. The live LLM is exercised ONLY by the human-run acceptance runbook; the unit suite stays hermetic (the adapter is contract-tested through an injectable `invoke`).

**Architecture:** `ClaudeCodeReasoner` satisfies the existing `Reasoner` protocol (`run(PMContext) -> PMCycleOutput`). It is split into pure, testable pieces — `render_prompt(context) -> str` and `parse_response(text) -> PMCycleOutput` — around a single side-effecting `invoke(prompt) -> str` (default: shell out to `claude -p ... --output-format text`, mirroring the existing `ClaudeCodeExecutor`). Tests inject a fake `invoke` returning a canned transcript, so no live LLM runs in CI. A parse failure raises `PMReasonerError`, which propagates out of `reasoner.run()` *before* `pm_beat`'s staging commit — so a bad cycle stages nothing and simply retries next beat.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest. No new dependencies (uses stdlib `subprocess`/`json`/`re`).

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** none added.
- **No live LLM in the suite:** every test injects a fake `invoke` (or a `FakeReasoner`). The real `claude` binary is invoked only by `ClaudeCodeReasoner._default_invoke`, which is never exercised by a test. The manual acceptance runbook is where a human drives the real LLM.
- **Reasoner returns data; machinery writes:** `ClaudeCodeReasoner.run` returns a `PMCycleOutput`; it performs NO substrate writes. `pm_beat` (unchanged from 2a) does all writing and enforces propose-only + idempotency.
- **Fail before the commit:** a malformed/empty LLM response raises `PMReasonerError` from `run()`; because `pm_beat` calls `reasoner.run()` *before* `write_staging`, nothing is staged or submitted on a parse failure. Do not change `pm_beat`.
- **Do not modify Phase 2a machinery** (`pm_agent.py`, `pm_reasoner.py` dataclasses, `pm_runner.py`) except the one additive import the CLI needs. The seam is fixed; this plan only adds the real implementation + wiring + acceptance.
- **Backward compatibility:** all existing tests stay green; existing CLI subcommands (`worker`/`dispatch`/`program`) unchanged.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only Phase 2b is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 2a — PM machinery | program entity; reasoner seam + FakeReasoner; kill-safe pm_beat; runner | DONE |
| **2b — PM real brain** (this plan) | `ClaudeCodeReasoner`, `coscience pm` CLI, acceptance runbook | **planned here** |
| (later) dashboard | internal oversight UI over programs/sprints/results/ledger | own cycle |

Design spec: `docs/superpowers/specs/2026-06-24-pm-agent-design.md` (Increment 5).

---

## File Structure

```
src/coscience/
  pm_claude.py     # NEW (T1): PMReasonerError, render_prompt, parse_response, ClaudeCodeReasoner
  cli.py           # MODIFY (T2): `coscience pm` subcommand + _make_pm_reasoner factory
tests/
  test_pm_claude.py        # NEW (T1): render/parse/run contract tests (injected invoke)
  test_cli_pm.py           # NEW (T2): `coscience pm` wiring (FakeReasoner via factory swap)
  test_pm_claude_e2e.py    # NEW (T3): canned realistic transcript -> ClaudeCodeReasoner -> pm_beat -> proposed sprint
docs/superpowers/plans/
  phase2b-pm-acceptance.md # NEW (T3): manual end-to-end acceptance runbook
```

---

## Task 1: `ClaudeCodeReasoner` adapter (`pm_claude.py`)

**Files:**
- Create: `src/coscience/pm_claude.py`
- Test: `tests/test_pm_claude.py`

**Interfaces:**
- Consumes: `PMContext`, `PMCycleOutput`, `ProposedSprint` from `coscience.pm_reasoner`.
- Produces:
  - `class PMReasonerError(Exception)`.
  - `render_prompt(context: PMContext) -> str` — a prompt embedding the program goals, the open sprints, the completed sprints + their result summaries, the prior proposals (with a "do not repeat these" instruction), and an explicit instruction to respond with ONLY a JSON object of the shape `{"report": str, "proposals": [{"suffix": str, "goals": str, "plan": [{"id": str, "run": str}], "priority": int, "resources_required": object|null, "rationale": str}]}`.
  - `parse_response(text: str) -> PMCycleOutput` — extracts the JSON object from the model text (handles a ```json fenced block or a bare `{...}`), builds a `PMCycleOutput`; optional proposal fields default (`priority=0`, `resources_required=None`, `rationale=""`); raises `PMReasonerError` on no-JSON / invalid-JSON / a proposal missing a required field (`suffix`/`goals`/`plan`).
  - `class ClaudeCodeReasoner` — `__init__(self, invoke=None, claude_bin="claude")`; `run(context) -> PMCycleOutput` = `parse_response(self._invoke(render_prompt(context)))`. `_invoke` defaults to a private `_default_invoke` that shells out to `claude -p <prompt> --output-format text` and raises `PMReasonerError` on a non-zero exit. Satisfies the `Reasoner` protocol.

- [ ] **Step 1: Write the failing tests**

`tests/test_pm_claude.py`:
```python
import json

import pytest

from coscience.pm_claude import (ClaudeCodeReasoner, PMReasonerError,
                                 parse_response, render_prompt)
from coscience.pm_reasoner import PMContext


def _ctx():
    return PMContext(
        program_id="p1", goals="cure cancer", cycle=2,
        open_sprints=[{"id": "p1-open", "status": "approved", "goals": "assay X"}],
        completed=[{"id": "p1-c0-a", "goals": "prior", "result": "found Y"}],
        prior_proposals=["p1-c0-a"])


def test_render_prompt_includes_state_and_json_instruction():
    p = render_prompt(_ctx())
    assert "cure cancer" in p
    assert "assay X" in p            # open sprint
    assert "found Y" in p            # completed result
    assert "p1-c0-a" in p            # prior proposal (don't repeat)
    assert "JSON" in p
    assert "proposals" in p and "suffix" in p   # schema cues


def test_parse_response_plain_json():
    text = json.dumps({"report": "looks good", "proposals": [
        {"suffix": "a", "goals": "do a", "plan": [{"id": "s", "run": "true"}],
         "priority": 3, "resources_required": {"gpu": 1}, "rationale": "because"}]})
    out = parse_response(text)
    assert out.report == "looks good"
    assert len(out.proposals) == 1
    p = out.proposals[0]
    assert (p.suffix, p.goals, p.priority) == ("a", "do a", 3)
    assert p.plan == [{"id": "s", "run": "true"}]
    assert p.resources_required == {"gpu": 1}
    assert p.rationale == "because"


def test_parse_response_fenced_json_and_optional_defaults():
    text = ("Here is my plan:\n```json\n"
            + json.dumps({"report": "r", "proposals": [
                {"suffix": "b", "goals": "g", "plan": [{"id": "s", "run": "true"}]}]})
            + "\n```\nThanks!")
    out = parse_response(text)
    assert out.proposals[0].priority == 0
    assert out.proposals[0].resources_required is None
    assert out.proposals[0].rationale == ""


def test_parse_response_no_json_raises():
    with pytest.raises(PMReasonerError):
        parse_response("I could not decide. No JSON here.")


def test_parse_response_invalid_json_raises():
    with pytest.raises(PMReasonerError):
        parse_response("{ not valid json )")


def test_parse_response_missing_required_field_raises():
    with pytest.raises(PMReasonerError):
        parse_response(json.dumps({"report": "r", "proposals": [{"goals": "g"}]}))


def test_run_uses_injected_invoke():
    canned = json.dumps({"report": "ok", "proposals": []})
    seen = {}

    def fake_invoke(prompt: str) -> str:
        seen["prompt"] = prompt
        return canned

    reasoner = ClaudeCodeReasoner(invoke=fake_invoke)
    out = reasoner.run(_ctx())
    assert out.report == "ok"
    assert "cure cancer" in seen["prompt"]   # render_prompt was used
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_claude.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.pm_claude'`.

- [ ] **Step 3: Implement `src/coscience/pm_claude.py`**

```python
"""Real PM reasoner: a headless Claude Code session behind the Reasoner seam.

render_prompt and parse_response are pure and contract-tested; the single
side-effecting step (invoke) shells out to the `claude` binary and is injectable
so the unit suite never calls a live LLM. A bad response raises PMReasonerError,
which propagates out of run() before pm_beat's staging commit (nothing staged)."""
from __future__ import annotations

import json
import re
import subprocess

from coscience.pm_reasoner import PMContext, PMCycleOutput, ProposedSprint


class PMReasonerError(Exception):
    """The reasoner produced no usable PMCycleOutput."""


def render_prompt(context: PMContext) -> str:
    def _lines(items, fmt):
        return "\n".join(fmt(i) for i in items) or "(none)"

    open_block = _lines(context.open_sprints,
                        lambda s: f"- {s['id']} [{s['status']}]: {s['goals']}")
    done_block = _lines(context.completed,
                        lambda s: f"- {s['id']}: {s['goals']} -> result: {s['result']}")
    prior_block = ", ".join(context.prior_proposals) or "(none)"
    return f"""You are the PM agent for a research program. Propose the next sprint(s)
and write a short status report. You only PROPOSE; humans approve.

PROGRAM GOALS:
{context.goals}

OPEN SPRINTS (already proposed/approved/running — do not duplicate these):
{open_block}

COMPLETED SPRINTS AND RESULTS (use these to decide what is most valuable next):
{done_block}

PRIOR PROPOSALS you already made (do NOT repeat their intent): {prior_block}

Respond with ONLY a JSON object (no prose outside it) of this shape:
{{"report": "<markdown program-status summary>",
  "proposals": [
    {{"suffix": "<short-slug>", "goals": "<what this sprint does>",
      "plan": [{{"id": "<step-id>", "run": "<shell command>"}}],
      "priority": <int>, "resources_required": {{}} or null,
      "rationale": "<why this experiment next>"}}
  ]}}
Propose 0 proposals if nothing new is warranted. Keep `plan` steps concrete and runnable.
"""


def _extract_json(text: str) -> str:
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise PMReasonerError("no JSON object found in reasoner output")
    return text[start:end + 1]


def parse_response(text: str) -> PMCycleOutput:
    try:
        data = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise PMReasonerError(f"invalid reasoner JSON: {exc}") from exc
    proposals = []
    for p in data.get("proposals", []):
        try:
            proposals.append(ProposedSprint(
                suffix=str(p["suffix"]), goals=str(p["goals"]),
                plan=[{"id": str(s["id"]), "run": str(s["run"])} for s in p["plan"]],
                priority=int(p.get("priority", 0)),
                resources_required=p.get("resources_required"),
                rationale=str(p.get("rationale", "")),
            ))
        except (KeyError, TypeError) as exc:
            raise PMReasonerError(f"malformed proposal: {exc}") from exc
    return PMCycleOutput(proposals=proposals, report=str(data.get("report", "")))


class ClaudeCodeReasoner:
    """Reasoner backed by a headless Claude Code session. `invoke` is injectable
    for testing; the default shells out to the `claude` binary."""

    def __init__(self, invoke=None, claude_bin: str = "claude"):
        self.claude_bin = claude_bin
        self._invoke = invoke or self._default_invoke

    def _default_invoke(self, prompt: str) -> str:
        proc = subprocess.run(
            [self.claude_bin, "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise PMReasonerError(
                f"claude exited {proc.returncode}: {(proc.stderr or '')[:200]}")
        return proc.stdout or ""

    def run(self, context: PMContext) -> PMCycleOutput:
        return parse_response(self._invoke(render_prompt(context)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_claude.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_claude.py tests/test_pm_claude.py
git commit -m "feat: ClaudeCodeReasoner (real PM brain behind the seam)"
```

---

## Task 2: `coscience pm` CLI subcommand

**Files:**
- Modify: `src/coscience/cli.py`
- Test: `tests/test_cli_pm.py`

**Interfaces:**
- Consumes: `pm_run_once`/`pm_loop` (Phase 2a), `ClaudeCodeReasoner` (Task 1), `Substrate`.
- Produces:
  - A module-level factory `_make_pm_reasoner()` in `cli.py` that returns `ClaudeCodeReasoner()` (tests monkeypatch this to inject a `FakeReasoner`, so no live LLM runs).
  - A `pm` subcommand: `coscience pm --repo R [--once | --loop] [--interval F] [--max-rounds N]`. `--once` (the default when neither flag is given) runs `pm_run_once(substrate, reasoner)` and prints one line per program summary. `--loop` runs `pm_loop(substrate, reasoner, interval=..., max_rounds=...)`. Mirrors the existing `worker`/`dispatch` loop idiom.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_pm.py`:
```python
import coscience.cli as cli
from coscience.models import Program
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.substrate import Substrate


def _seed_program(tmp_path):
    Substrate(tmp_path).save_program(Program(id="p1", title="C", goals="cure"))


def _fake_reasoner_factory(outputs):
    return lambda: FakeReasoner(list(outputs))


def test_pm_once_proposes(tmp_path, monkeypatch, capsys):
    _seed_program(tmp_path)
    out = PMCycleOutput(proposals=[ProposedSprint(suffix="a", goals="do a",
                                                 plan=[{"id": "s", "run": "true"}])],
                        report="r")
    monkeypatch.setattr(cli, "_make_pm_reasoner", _fake_reasoner_factory([out]))

    rc = cli.main(["pm", "--repo", str(tmp_path), "--once"])
    assert rc == 0
    sprint = Substrate(tmp_path).load_sprint("p1-c0-a")
    assert sprint.goals == "do a"
    assert "p1" in capsys.readouterr().out          # printed a summary line


def test_pm_loop_runs_max_rounds(tmp_path, monkeypatch):
    _seed_program(tmp_path)
    outs = [PMCycleOutput(report="r1"), PMCycleOutput(report="r2")]
    monkeypatch.setattr(cli, "_make_pm_reasoner", _fake_reasoner_factory(outs))
    # avoid real sleeping between rounds
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    rc = cli.main(["pm", "--repo", str(tmp_path), "--loop", "--max-rounds", "2"])
    assert rc == 0
    assert Substrate(tmp_path).load_pm_state("p1").cycle == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_cli_pm.py -v`
Expected: FAIL (`AttributeError: module 'coscience.cli' has no attribute '_make_pm_reasoner'` / no `pm` subcommand).

- [ ] **Step 3: Implement the `pm` subcommand**

In `src/coscience/cli.py`:
- Add imports near the top:
```python
from coscience.pm_claude import ClaudeCodeReasoner
from coscience.pm_runner import pm_loop, pm_run_once
```
- Add the factory (module level, after `dispatch_once`):
```python
def _make_pm_reasoner():
    return ClaudeCodeReasoner()
```
- Register the subcommand (after the `program` parser, before `args = parser.parse_args(argv)`):
```python
    pm = sub.add_parser("pm", help="run the PM agent: propose sprints for active programs")
    pm.add_argument("--repo", required=True, type=Path)
    pmmode = pm.add_mutually_exclusive_group()
    pmmode.add_argument("--once", action="store_true")
    pmmode.add_argument("--loop", action="store_true")
    pm.add_argument("--interval", type=float, default=5.0)
    pm.add_argument("--max-rounds", type=int, default=None)
```
- Handle it (after the `program` handler block):
```python
    if args.command == "pm":
        substrate = Substrate(args.repo)
        reasoner = _make_pm_reasoner()
        if args.once or not args.loop:
            for summary in pm_run_once(substrate, reasoner):
                print(f"{summary['program']}: cycle={summary['cycle']} "
                      f"submitted={summary['submitted']}", flush=True)
            return 0
        rounds = pm_loop(substrate, reasoner, interval=args.interval,
                         max_rounds=args.max_rounds)
        print(f"pm ran {rounds} rounds", flush=True)
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass, then the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_cli_pm.py -v`
Expected: 2 passed.

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass. Record the count.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/cli.py tests/test_cli_pm.py
git commit -m "feat: coscience pm CLI (wire ClaudeCodeReasoner into the runner)"
```

---

## Task 3: End-to-end canned-transcript test + acceptance runbook

**Files:**
- Create: `tests/test_pm_claude_e2e.py`
- Create: `docs/superpowers/plans/phase2b-pm-acceptance.md`

**Interfaces:**
- No new production code. Proves a realistic LLM transcript flows through `ClaudeCodeReasoner` (injected invoke) and `pm_beat` into an on-disk proposed sprint + report — the same path the live run takes, minus the live LLM. Plus a human runbook for the real end-to-end loop.

- [ ] **Step 1: Write the end-to-end test**

`tests/test_pm_claude_e2e.py`:
```python
from coscience.models import Program, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_claude import ClaudeCodeReasoner

# A realistic model reply: prose around a fenced JSON block.
TRANSCRIPT = """Looking at the program, the next experiment should test dosage.

```json
{"report": "## Status\\nOne assay done; proposing a dose-response follow-up.",
 "proposals": [
   {"suffix": "dose-response", "goals": "Run a dose-response assay",
    "plan": [{"id": "run", "run": "echo dose-response"}],
    "priority": 2, "resources_required": {"gpu": 1}, "rationale": "highest value next"}]}
```
That is my recommendation.
"""


def test_canned_transcript_flows_through_pm_beat(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure cancer"))
    reasoner = ClaudeCodeReasoner(invoke=lambda prompt: TRANSCRIPT)

    summary = pm_beat(substrate, "p1", reasoner)

    sid = "p1-c0-dose-response"
    assert summary["submitted"] == [sid]
    sprint = substrate.load_sprint(sid)
    assert sprint.status == SprintStatus.PROPOSED       # propose-only
    assert sprint.program == "p1"
    assert sprint.priority == 2
    assert sprint.resources_required == {"gpu": 1.0}
    assert "dose-response" in substrate.load_report("p1")
```

- [ ] **Step 2: Run it (red, then green after confirming imports)**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pm_claude_e2e.py -v`
Expected: PASS (all production code already exists from Tasks 1–2 and Phase 2a). If it fails, the failure points at a real integration gap between `ClaudeCodeReasoner` and `pm_beat` — fix the production code, not the test.

- [ ] **Step 3: Run the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass. Record the count.

- [ ] **Step 4: Write the acceptance runbook**

Create `docs/superpowers/plans/phase2b-pm-acceptance.md` — the human-run end-to-end proof (this is where the LIVE LLM runs):
```markdown
# Phase 2b — PM agent live acceptance runbook

Prereqs: the `claude` CLI is installed and authenticated; run from the repo root;
use a scratch repo dir, e.g. `R=/tmp/coscience-pm-demo`. Test interpreter/venv:
`/home/oleg/venvs/coscience/bin/coscience` (the `coscience` console script).

1. Create a program:
   `coscience program create --repo $R --id p1 --title "Demo" --goals "Find the smallest prime gap above 1e6 by brute force"`
2. Run ONE real PM cycle (this calls the live `claude`):
   `coscience pm --repo $R --once`
   -> prints `p1: cycle=0 submitted=[...]`; the model proposed at least one sprint.
3. Inspect what it proposed (machinery wrote it, status must be `proposed`):
   `coscience` has no read cmd — use the HTTP API: in another shell,
   `COSCIENCE_REPO=$R COSCIENCE_HOST=127.0.0.1 coscience-http` then
   `curl -s localhost:8000/programs/p1 | jq` (see the report + sprint list) and
   `curl -s 'localhost:8000/sprints?status=proposed' | jq`.
4. Read the report the PM wrote: `cat $R/programs/p1/report.md`.
5. Approve one proposed sprint (human gate):
   `curl -s -X POST localhost:8000/sprints/<sprint-id>/approve`  -> status "approved".
6. Run the dispatcher to execute it:
   `coscience dispatch --repo $R --once`  (add `--executor claude` only if a step needs the LLM).
   Re-run `--once` until the sprint reaches `done` and a result file appears under `$R/results/`.
7. Run the PM again so it REACTS to the new result:
   `coscience pm --repo $R --once`
   -> cycle=1; the new report references the completed work; any follow-up proposal avoids repeating prior ones.
8. Confirm the loop closed: `cat $R/programs/p1/pm.md` shows cycle=2 and the proposed-id history.

If the model returns malformed JSON, the beat raises PMReasonerError, stages nothing,
and the next `coscience pm --once` simply retries — no partial state is written.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_pm_claude_e2e.py docs/superpowers/plans/phase2b-pm-acceptance.md
git commit -m "test: PM real-brain end-to-end (canned transcript) + acceptance runbook"
```

---

## Self-Review

**Spec coverage (Increment 5 of `2026-06-24-pm-agent-design.md`):**
- `ClaudeCodeReasoner` (prompt render + JSON parse), behind the seam → Task 1 ✓
- contract-tested on a canned transcript; live LLM not in the unit suite → Tasks 1 + 3 ✓
- `coscience pm` CLI wiring it into the runner → Task 2 ✓
- manual acceptance runbook (create program → PM proposes → human approves → dispatcher runs → result → PM reacts) → Task 3 ✓
- parse failure aborts before the staging commit → Global Constraints + Task 1 (`run` raises, `pm_beat` unchanged) ✓
- propose-only preserved (machinery writes, status PROPOSED) → re-asserted by the e2e test ✓

**Placeholder scan:** complete code in every step; real assertions; no TBD. ✓

**Type consistency:** `render_prompt(PMContext)->str`; `parse_response(str)->PMCycleOutput`; `PMReasonerError`; `ClaudeCodeReasoner(invoke=None, claude_bin="claude").run(PMContext)->PMCycleOutput`; `_make_pm_reasoner()`; CLI `pm` uses `pm_run_once`/`pm_loop` from Phase 2a. ✓

**Known simplifications (intentional):**
- The prompt is a single-shot text prompt (no MCP tool access for the reasoner) — context goes in, JSON comes out, matching the spec's first-build decision.
- `_default_invoke` uses `claude -p ... --output-format text` exactly like the existing `ClaudeCodeExecutor`; no streaming/session reuse.
- The acceptance runbook drives the live LLM by hand; CI never does.
```
