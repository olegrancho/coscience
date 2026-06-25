# Co-Science Platform — PM Agent Design (Phase 2, first iteration)

**Status:** approved design, ready for planning
**Date:** 2026-06-24
**Scope:** the **PM agent** only. The internal **dashboard** is deferred to its own spec → plan → build cycle (it is far more useful once a PM agent is producing proposals and reports to display).

## 1. Purpose

The platform turns research **programs** (a human-stated research direction) into **sprints** (units of work) that **worker** sessions execute, producing **results**. Everything below the program level already exists (substrate, scheduler/leases, dispatcher, worker heartbeat, the transport-agnostic `Service` + MCP + HTTP). What is missing is the **program-level coordinator**: something that looks at a program's goals and what has been learned so far and decides what to do next.

The **PM agent** fills that role. Each cycle it: reads the program's goals + current sprint/result state, **proposes** the next sprint(s), and rewrites a human-readable **program-status report**. It is the irreducible "PM" job that nothing else in the system does.

### Core responsibilities (this iteration)
- **Plan** — turn a program's goals into proposed sprints.
- **Analyze** — read completed results and propose follow-on sprints (the feedback loop that makes it an ongoing PM, not a one-shot planner).
- **Report** — write a human-readable program-status summary each cycle (the artifact that makes human approval fast).

### Explicitly out of scope (this iteration)
- **Approving** sprints (see Autonomy below) — humans do this.
- **Monitoring/ops** of running sprints — the dispatcher's reconciliation already owns physical job state.
- The **dashboard** — separate future cycle.
- Giving the reasoner **live MCP tool access** — context goes in, structured output comes out; live read-tools are a clean later enhancement.

## 2. Autonomy boundary (safety-critical)

**Propose-only. The PM never approves its own work.** It writes sprints as `status="proposed"`; a human (the oversight committee), through the existing `Service`/transports, approves — which is what makes a sprint eligible to consume resources. This keeps a human gate on **every unit of real compute**, consistent with the platform's oversight ethos and the deliberate `submit`/`approve` split already in the service.

The PM's **report** is precisely the decision aid that makes that approval gate cheap to operate.

## 3. Architecture overview

The PM is a **durable heartbeat agent**, uniform with the workers: durable identity, disposable session, all state on disk, one bounded step per beat, survives kill via an idempotent resume. It runs in its **own loop**, independent of the resource-leasing dispatcher.

Three independent loops over one git-backed substrate:

```
coscience pm  (propose)   ‖   humans (approve)   ‖   coscience dispatch (run)
        \________________________ shared substrate (programs, sprints, results) ______________/
```

The PM and dispatcher never touch each other's state except through shared sprint records. The PM is **not resource-leased** — it is a lightweight coordinator (read state + one LLM call + write proposals), so it stays out of the ledger entirely.

### The reasoner seam (the safety crux)

The LLM produces **structured data**; the deterministic machinery does **all writes**. The LLM never writes, so it cannot bypass propose-only or double-submit — it returns a value and the machinery enforces the rules.

```python
@dataclass
class PMContext:        # built by the machinery, handed to the reasoner
    program_id: str
    goals: str
    cycle: int
    open_sprints: list[dict]      # proposed/approved/executing: {id, status, goals}
    completed: list[dict]         # done sprints + result summaries: {id, goals, result}
    prior_proposals: list[str]    # sprint ids already proposed (so it won't repeat)

@dataclass
class ProposedSprint:
    suffix: str                   # short slug; full id derived deterministically (see §5)
    goals: str
    plan: list[dict]              # steps [{id, run}]
    priority: int = 0
    resources_required: dict | None = None
    rationale: str = ""

@dataclass
class PMCycleOutput:
    proposals: list[ProposedSprint]
    report: str                   # full markdown program-status summary

class Reasoner(Protocol):
    def run(self, context: PMContext) -> PMCycleOutput: ...
```

- `FakeReasoner` — scripted outputs; powers the deterministic unit suite.
- `ClaudeCodeReasoner` — renders a prompt from `PMContext`, runs a Claude Code session via the existing `ClaudeCodeExecutor`, parses a structured JSON block back into `PMCycleOutput`. Validated by a manual runbook + a focused prompt-render/JSON-parse contract test on a canned transcript — the live LLM is **not** in the unit suite.

This mirrors the project's established `ShellStepExecutor` (tests) vs `ClaudeCodeExecutor` (real) discipline: correctness-critical bookkeeping lives in tested Python; the LLM does only the creative judgment (what is the next good experiment).

## 4. Substrate: the `program` entity

A new first-class entity, stored like sprints:

```
programs/<id>/
  program.md   # frontmatter: id, title, status (active|paused|closed); body = research goals (human-authored)
  report.md    # PM-authored program-status summary, rewritten each cycle
  pm.md        # PM working state: cycle count, last-run, proposed-id ledger, short rationale log (memory)
  .pm/cycle-staging.json   # transient: the committed PMCycleOutput of an in-flight beat (see §5)
```

- Sprints already carry a `program` field — they link to a program; results reach the program via their sprint.
- New model types: `Program` (id, title, status, goals) and `PMState` (cycle, last_run, proposed_ids, log).
- `Substrate` gains: `load_program`, `iter_programs(status=None)`, `save_program`, `save_report`, `load_pm_state`, `save_pm_state`. The staging file is read/written by the PM core (atomic write-then-rename).
- **No change** to existing sprint/result/lease storage.
- Programs are exposed read-only through `Service` (and thus MCP/HTTP) so humans — and the future dashboard — can list/inspect them and their reports. (Program *creation* is human-authored: write `program.md`, or a thin `submit_program`/`create-program` path; see Increment 1.)

## 5. The PM heartbeat: one bounded, kill-safe beat

A beat is one PM cycle. Because a real LLM is **non-deterministic**, naively re-running a killed cycle could submit *different* proposals than the half-submitted ones. The non-determinism is fenced behind a **staging commit** — the PM's analogue of the worker's step-checkpoint.

```
pm_beat(program):
  1. gather PMContext from substrate (goals, open/completed sprints, prior proposals)
  2. if no staged cycle on disk:
        out = reasoner.run(context)                       # the ONE non-deterministic call
        atomically write out -> programs/<id>/.pm/cycle-staging.json     # COMMIT POINT
  3. for each proposal in the staged output:
        sprint_id = f"{program_id}-c{cycle}-{suffix}"      # deterministic
        if a sprint with that id already exists: skip      # idempotent
        else: submit it as status="proposed", program=program_id
  4. write report.md from the staged report
  5. record proposed ids + bump cycle in pm.md; clear the staging file
```

Kill anywhere; the next beat resumes correctly:
- killed **before** staging → nothing submitted; re-run fresh.
- killed **after** staging, mid-submit → resume reads the *staged* output and finishes submitting — **no re-reasoning, so no drift**.
- killed after submit, before report → re-submits skipped (ids exist); report written.

The LLM runs **at most once per cycle**; every write is idempotent. The deterministic id (`<program>-c<cycle>-<suffix>`) is the idempotency key. Submission uses an **existence check in the PM** (skip if the sprint id already exists) so the `Service`/`submit_sprint` contract stays unchanged.

## 6. Runner & wiring

- `coscience pm` — a loop runner (mirrors `coscience worker`) that beats each **active** program on a cadence.
- `pm_beat(program, reasoner)` — the single-shot core the loop and tests call, and that a human/cron could invoke once.
- The PM runner takes a `Reasoner`: the loop wires `ClaudeCodeReasoner`; tests wire `FakeReasoner`.
- No new coupling: PM proposes → humans approve → dispatcher runs. Independent loops.

## 7. Testing & acceptance

**Unit suite (hermetic, deterministic — `FakeReasoner` throughout):**
- program CRUD in the substrate; round-trip of `Program`/`PMState`.
- `PMContext` is gathered correctly (goals; open vs completed sprints; result summaries; prior proposals).
- a beat submits the staged proposals as `status="proposed"` linked to the program; `report.md` written; `pm.md` updated (cycle bumped, ids recorded).
- **idempotency** — re-running a beat skips existing ids; no duplicates.
- **kill/resume** — (a) a staging file present (mid-cycle) ⇒ resume submits from it without calling the reasoner; (b) partial submission ⇒ no duplicate sprints; (c) report-not-yet-written ⇒ report appears on resume.
- No Claude, no network anywhere in the suite.

**Real adapter:** `ClaudeCodeReasoner` gets a focused contract test for prompt-render + JSON-parse fed a canned session transcript. The live LLM is not unit-tested.

**Manual acceptance runbook** (sibling doc, like the container acceptance): create a program with goals → run one real `pm` beat → confirm proposed sprints appear (via `coscience` / HTTP `/docs`) → a human approves one → `coscience dispatch` runs it → a result appears → the next PM beat reacts to that result. This is the end-to-end "the loop closes" proof.

## 8. Decomposition into increments

Built subagent-driven, one agent at a time, TDD, same method as Phase 1b. Each increment ships green and is independently reviewable; the real LLM only enters at Increment 5, behind the seam the first four were built and tested against.

1. **Program substrate** — `Program`/`PMState` models, `programs/<id>/` storage (`load/iter/save_program`, `save_report`, `load/save_pm_state`), and read-only program exposure through `Service` (+ MCP/HTTP) so programs and reports are visible.
2. **Reasoner seam + context** — the dataclasses (`PMContext`, `ProposedSprint`, `PMCycleOutput`), the `Reasoner` protocol, `FakeReasoner`, and the pure `PMContext` gatherer over the substrate.
3. **PM heartbeat core** — `pm_beat` with staged-commit kill-safety, idempotent submit, report + `pm.md` persistence. The bulk of the deterministic tests live here.
4. **Runner + CLI** — `coscience pm` loop over active programs; single-shot wiring; reasoner injection.
5. **Real adapter + acceptance** — `ClaudeCodeReasoner` (prompt render + JSON parse, contract-tested) behind the seam, plus the manual acceptance runbook.

## 9. Key decisions (resolved during brainstorming)

| Decision | Choice | Why |
|---|---|---|
| Scope of this cycle | PM agent only; dashboard later | Load-bearing, higher-risk piece; gives the dashboard real content to show. |
| PM core job | Plan + analyze + report | The irreducible PM loop plus the human-facing summary. |
| Autonomy | Propose-only; humans approve | Human gate on every unit of compute; matches the submit/approve split. |
| Cadence | Durable heartbeat agent | Architecturally uniform with workers; reacts to new results continuously. |
| `program` representation | First-class substrate entity | Durable home for goals, report, and PM memory. |
| Reasoner | Seam + real adapter; deterministic fake for tests | Correctness-critical bookkeeping in tested Python; LLM does only judgment. |
| PM resource use | Not leased; own loop | Lightweight coordinator; no ledger coupling. |

## 10. Risks & mitigations

- **Non-deterministic reasoner breaking resume** → staged-commit fences the single LLM call; resume replays staged output, never re-reasons.
- **Proposal duplication across cycles** → deterministic ids + existence-check skip; the reasoner is also told its prior proposals.
- **Reasoner returns malformed output** → `ClaudeCodeReasoner` parse is contract-tested; a parse failure aborts the beat *before* the staging commit (nothing submitted), to retry next beat.
- **Over-proposing / runaway** → propose-only means humans still gate compute; a per-cycle proposal cap can be added in the PM core if needed (noted, not built this iteration).
- **Program creation surface** → kept minimal (human-authored `program.md` / thin create path); not an open submission endpoint in this iteration.
