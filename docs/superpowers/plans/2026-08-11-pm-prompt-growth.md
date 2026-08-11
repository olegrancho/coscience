# PM Prompt Growth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the PM prompt growing with a program's history, make its real token cost visible in the run ledger, and reserve usage headroom so autonomous loops cannot eat the window a human wants.

**Architecture:** Every prompt-shrinking change happens in `render_prompt` (`pm_claude.py`), which is pure and already contract-tested — **not** in `gather_context`. That is deliberate: `context_fingerprint` hashes `_context_payload`, so shrinking the context object would change every program's fingerprint and wake all four programs at once on the first beat after deploy. Rendering-side changes leave the fingerprint untouched. Older completed/failed sprints keep a one-line entry carrying their id (rather than being dropped) so lineage-graph references and `release_ids`/`reopen_ids` instructions still resolve against ids the prompt actually shows. Recency comes from `sprint.status_history[-1]["at"]`, which `set_status` already stamps.

**Tech Stack:** Python 3.12, pytest. No new dependencies.

## Global Constraints

- Run tests with `~/venvs/coscience/bin/python -m pytest` (the venv is uv-created and has no `pip`, only `pip3`).
- Branch: `feat/pm-prompt-growth`. Never commit outside it; never push without explicit approval.
- **`_context_payload` / `context_fingerprint` must not change behaviour.** `tests/test_program_instructions.py` and `tests/test_guidance_threads.py` guard this. If a change makes an existing program's fingerprint differ from what it was before the change, the design is wrong — move the change into `render_prompt`.
- `gather_context` may gain *keys* on its dicts (the fingerprint reads only `(id, result)` from `completed`), but must not remove or alter existing ones.
- Existing style: no docstring on every function, a short one where the *why* is non-obvious; comments explain rationale, not mechanics.
- No new dependencies, no new config files. New tunables are module-level constants.
- Measured baseline to beat (p3 Abiogenesis, 6 completed sprints): prompt seed **129,071 B**, of which the completed block is **53,458 B**. Per-completed-sprint marginal cost **8,910 B**.

---

## Phase 1 — See it (Tasks 1–2)

Instrumentation first, so Phase 2's effect is measurable rather than asserted.

### Task 1: The run ledger records prompt size and failures

Today `runs.jsonl` stores the *product* (total tokens) and none of the factors, so "the prompt grew" is indistinguishable from "the agent went exploring" — they need opposite fixes. A call that raises is recorded not at all, so a failing loop burns the window while leaving no trace.

**Files:**
- Modify: `src/coscience/usage_meter.py:36-52` (`record_run`), `src/coscience/usage_meter.py:75-87` (`run_stats`)
- Test: `tests/test_usage_meter.py`

**Interfaces:**
- Produces: `record_run(repo_root, kind, ref="", *, cost=None, tokens=None, model="", prompt_bytes=None, ok=True)`. Task 2 passes `prompt_bytes` and `ok`, and adds one more keyword — `turns=None` — once the reasoner can supply it.
- Produces: `run_stats()` aggregates gain a `"failed"` key (int) per kind.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_usage_meter.py`:

```python
def test_record_run_stores_prompt_bytes_and_failure(tmp_path):
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=100, prompt_bytes=4096)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=50, ok=False)

    rows = usage_meter.load_runs(tmp_path)
    assert rows[0]["prompt_bytes"] == 4096
    assert "ok" not in rows[0]          # success stays the absent default — old rows read as ok
    assert rows[1]["ok"] is False
    assert "prompt_bytes" not in rows[1]  # unknown values are omitted, never zero-filled


def test_run_stats_counts_failed_calls(tmp_path):
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=100)
    usage_meter.record_run(tmp_path, "pm", "p1", tokens=50, ok=False)

    stats = usage_meter.run_stats(tmp_path)
    assert stats["pm"]["total"] == 2
    assert stats["pm"]["failed"] == 1
    assert stats["worker"]["failed"] == 0
```

- [ ] **Step 2: Update the existing exact-equality test**

`tests/test_usage_meter.py::test_run_stats_empty` asserts the aggregate dict exactly, so a new key breaks it. Change its `empty` dict to:

```python
    empty = {"total": 0, "last_hour": 0, "last_day": 0, "last": None,
             "cost": 0, "cost_day": 0, "tokens": 0, "failed": 0}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_meter.py -v`
Expected: the two new tests FAIL (`TypeError: record_run() got an unexpected keyword argument 'prompt_bytes'`), and `test_run_stats_empty` FAILs on the missing `failed` key.

- [ ] **Step 4: Implement**

In `src/coscience/usage_meter.py`, change the `record_run` signature and body:

```python
def record_run(repo_root, kind: str, ref: str = "", *, cost=None, tokens=None,
               model: str = "", prompt_bytes=None, ok: bool = True) -> None:
    """Append one Claude-call record. `kind` is 'pm' or 'worker'; `ref` is the
    program or sprint id. `cost` (USD), `tokens`, and `model` are recorded when
    known (the agent reports them on a clean run). `prompt_bytes` is the rendered
    prompt we sent — without it the total is unattributable, since a beat's cost is
    roughly the prompt multiplied by however many turns the agent took. `ok=False`
    records a call that raised: it still burned the window, and a call that leaves
    no row makes a retry loop invisible in the ledger. Best-effort — never let
    logging break a beat."""
    try:
        rec = {"ts": time.time(), "kind": kind, "ref": ref}
        if cost is not None:
            rec["cost"] = float(cost)
        if tokens is not None:
            rec["tokens"] = int(tokens)
        if model:
            rec["model"] = model
        if prompt_bytes is not None:
            rec["prompt_bytes"] = int(prompt_bytes)
        if not ok:
            rec["ok"] = False          # absent == succeeded, so existing rows still read correctly
        path = _runs_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
```

In `run_stats`, add one line to the `agg` return dict, after `"tokens"`:

```python
            "failed": sum(1 for r in rs if r.get("ok") is False),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_meter.py -v`
Expected: PASS, all four tests.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/usage_meter.py tests/test_usage_meter.py
git commit -m "feat(usage): record prompt size and failed calls in the run ledger"
```

---

### Task 2: The PM reports its prompt size, and failed cycles are recorded

`ClaudeCodeReasoner.run` renders the prompt internally, so `pm_agent` cannot see how big it was. And `reasoner.run` raising at `pm_agent.py:534` skips `record_run` entirely — the most common failure is a malformed-JSON `parse_response` raise *after* a full agentic session has already burned its tokens.

**Files:**
- Modify: `src/coscience/pm_claude.py:387-391` (`__init__`), `:419-430` (`run`)
- Modify: `src/coscience/pm_agent.py:534-538`
- Test: `tests/test_pm_claude.py`, `tests/test_pm_beat.py`

**Interfaces:**
- Consumes: `record_run(..., prompt_bytes=, ok=)` from Task 1.
- Produces: `ClaudeCodeReasoner.last_prompt_bytes: int | None` — set by `run()` before invoking, so it is populated even when the call raises.
- Produces: `last_cost` gains a `"turns"` key, and `record_run` a `turns=None` keyword.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pm_claude.py`:

```python
def test_reasoner_reports_prompt_size_and_resets_stale_cost():
    """last_prompt_bytes must be set before the call, so a raising call still
    reports what it sent; last_cost must be cleared so a failure cannot report the
    PREVIOUS call's cost as its own."""
    r = ClaudeCodeReasoner(invoke=lambda p, m="", c="": '{"report": "ok"}')
    r.run(_ctx())
    assert r.last_prompt_bytes == len(render_prompt(_ctx()))

    r.last_cost = {"cost": 9.99, "tokens": 1}       # stale value from the good call
    def _boom(p, m="", c=""):
        raise PMReasonerError("claude exited 1")
    r._invoke = _boom
    with pytest.raises(PMReasonerError):
        r.run(_ctx())
    assert r.last_cost is None                       # not 9.99
    assert r.last_prompt_bytes == len(render_prompt(_ctx()))
```

`tests/test_pm_claude.py` already imports `pytest`, `ClaudeCodeReasoner`, `PMReasonerError` and `render_prompt` — no new imports needed.

Append to `tests/test_pm_beat.py` (which already imports `pytest` and `Program`):

```python
def test_failed_reasoner_call_is_recorded_in_the_ledger(substrate):
    from coscience import usage_meter
    from coscience.pm_claude import PMReasonerError

    substrate.save_program(Program(id="p1", title="P", goals="g"))

    class Boom:
        last_cost = {"cost": 0.5, "tokens": 1234}
        last_prompt_bytes = 4096
        def run(self, ctx):
            raise PMReasonerError("bad json")

    with pytest.raises(PMReasonerError):
        pm_beat(substrate, "p1", Boom())

    rows = usage_meter.load_runs(substrate.repo_root)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["tokens"] == 1234          # the session burned these before it raised
    assert rows[0]["prompt_bytes"] == 4096
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_claude.py tests/test_pm_beat.py -v`
Expected: FAIL — `AttributeError: 'ClaudeCodeReasoner' object has no attribute 'last_prompt_bytes'`, and the beat test finds zero ledger rows.

- [ ] **Step 3: Implement the reasoner side**

In `src/coscience/pm_claude.py`, extend `__init__`:

```python
    def __init__(self, invoke=None, claude_bin: str = "claude"):
        self.claude_bin = claude_bin
        self._invoke = invoke or self._default_invoke
        self.last_cost: dict | None = None     # {cost, tokens} of the most recent call
        self.last_prompt_bytes: int | None = None   # size of the prompt that call sent
```

and rewrite `run`:

```python
    def run(self, context: PMContext) -> PMCycleOutput:
        prompt = render_prompt(context)
        # Stamp size BEFORE invoking and clear the previous call's cost: a call that
        # raises must report its own prompt and no cost at all, never the last good
        # call's numbers.
        self.last_prompt_bytes = len(prompt)
        self.last_cost = None
        # Injected invokes (tests) may take fewer args; degrade prompt+model+cwd ->
        # prompt+model -> prompt so the seam stays easy to fake.
        try:
            out = self._invoke(prompt, context.model, context.workdir)
        except TypeError:
            try:
                out = self._invoke(prompt, context.model)
            except TypeError:
                out = self._invoke(prompt)
        return parse_response(out)
```

- [ ] **Step 4: Capture the turn count too**

A beat's total is roughly the prompt multiplied by however many turns the agent took, so the turn count is the other factor. The envelope `_default_invoke` already parses for cost carries it. In `src/coscience/pm_claude.py:410-415`, extend the `last_cost` assignment:

```python
            self.last_cost = {"cost": env.get("total_cost_usd"),
                              "turns": env.get("num_turns"),
                              "tokens": sum(int(usage.get(k, 0) or 0) for k in (
                                  "input_tokens", "output_tokens",
                                  "cache_creation_input_tokens", "cache_read_input_tokens"))}
```

`env.get("num_turns")` is `None` if this `claude` build does not report it, and `record_run` omits `None` values — so an absent field costs nothing. Add the passthrough to `record_run` in `usage_meter.py` beside `prompt_bytes`:

```python
        if turns is not None:
            rec["turns"] = int(turns)
```

with `turns=None` added to the keyword-only parameters.

- [ ] **Step 5: Implement the beat side**

In `src/coscience/pm_agent.py`, replace lines 534-538 (the `reasoner.run` call and the `record_run` that follows it) with:

```python
        def _record(ok: bool) -> None:
            lc = getattr(reasoner, "last_cost", None) or {}
            usage_meter.record_run(substrate.repo_root, "pm", program_id,
                                   cost=lc.get("cost"), tokens=lc.get("tokens"),
                                   turns=lc.get("turns"), model=context.model,
                                   prompt_bytes=getattr(reasoner, "last_prompt_bytes", None),
                                   ok=ok)
        try:
            output = reasoner.run(context)             # the ONE reasoner call
        except Exception:
            # The session ran and spent the window before it raised (a malformed-JSON
            # parse is the common case). A call that leaves no row makes a retry loop
            # invisible in the ledger — see Task 8.
            _record(ok=False)
            raise
        _record(ok=True)
```

- [ ] **Step 6: Run the full suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS, 825 tests (823 baseline + the 2 new in this task; Task 1's are already counted).

- [ ] **Step 7: Commit**

```bash
git add src/coscience/pm_claude.py src/coscience/pm_agent.py tests/test_pm_claude.py tests/test_pm_beat.py
git commit -m "feat(pm): report prompt size and record cycles that failed"
```

---

## Phase 2 — Shrink it (Tasks 3–5)

Mergeable on its own. Task 5 is the guard that keeps Tasks 3–4 from silently regressing.

### Task 3: Sprint history carries a finish time and a title

`ctx.completed` is built by iterating `substrate.iter_sprints()`, whose order is directory order, not chronological — so "the most recent N" is not expressible yet. `set_status` already stamps `status_history[-1]["at"]`; surface it. A `title` gives the collapsed one-line form something readable to show.

**Files:**
- Modify: `src/coscience/pm_agent.py:158-198` (`gather_context`)
- Test: `tests/test_pm_context.py`

**Interfaces:**
- Produces: entries in `PMContext.completed` and `PMContext.failed` gain `"title": str` and `"finished_at": float`. `completed` and `failed` are each sorted oldest-first by `finished_at`. Task 4 relies on both.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pm_context.py`:

```python
def test_completed_sprints_carry_title_and_sort_oldest_first(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    for sid, at in (("p1-late", 200.0), ("p1-early", 100.0)):
        s = Sprint(id=sid, status=SprintStatus.DONE, goals="g", plan=["x"],
                   program="p1", title=f"T-{sid}", results=[f"{sid}-r"])
        s.status_history = [{"status": "done", "at": at, "by": "", "action": ""}]
        substrate.save_sprint(s)
        substrate.save_result(Result(id=f"{sid}-r", sprint=sid, summary="found X"))

    ctx = gather_context(substrate, "p1")
    assert [s["id"] for s in ctx.completed] == ["p1-early", "p1-late"]
    assert ctx.completed[0]["title"] == "T-p1-early"
    assert ctx.completed[1]["finished_at"] == 200.0
```

- [ ] **Step 2: Update the two existing exact-equality assertions**

Adding keys breaks two tests that compare `ctx.completed` as a whole dict. In `tests/test_pm_context.py`:

```python
# in test_gather_context_splits_open_and_completed — was:
#   assert ctx.completed == [{"id": "p1-done", "goals": "prior", "result": "found X"}]
    assert ctx.completed == [{"id": "p1-done", "goals": "prior", "result": "found X",
                              "title": "", "finished_at": 0.0}]

# in test_gather_context_done_without_result — was:
#   assert ctx.completed == [{"id": "p1-d", "goals": "d", "result": ""}]
    assert ctx.completed == [{"id": "p1-d", "goals": "d", "result": "",
                              "title": "", "finished_at": 0.0}]
```

- [ ] **Step 3: Run to verify failure**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_context.py -v`
Expected: FAIL — the new test on the missing `title` key, the two updated ones on the dicts not yet carrying the new keys.

- [ ] **Step 4: Implement**

In `src/coscience/pm_agent.py`, add a module-level helper above `gather_context`:

```python
def _finished_at(sprint) -> float:
    """When this sprint reached its terminal state. `set_status` stamps every
    transition, so the last entry is the finish; 0.0 for records written before
    status history existed, which sorts them oldest."""
    hist = sprint.status_history or []
    try:
        return float(hist[-1].get("at") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0
```

Inside `gather_context`, change the two append sites:

```python
            completed.append({"id": s.id, "goals": s.goals, "result": result,
                              "title": s.title, "finished_at": _finished_at(s)})
```

```python
            failed.append({"id": s.id, "goals": s.goals, "error": err,
                           "title": s.title, "finished_at": _finished_at(s)})
```

Then sort both, immediately before the `guidance_threads = ...` line:

```python
    # Oldest first, so "the most recent N" is expressible when the prompt is rendered.
    completed.sort(key=lambda s: s["finished_at"])
    failed.sort(key=lambda s: s["finished_at"])
```

- [ ] **Step 5: Run the full suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS. `_context_payload` reads only `(id, result)` from `completed` and `(id, error)` from `failed`, so the fingerprint is unchanged — `tests/test_program_instructions.py` and `tests/test_guidance_threads.py` prove it.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_pm_context.py
git commit -m "feat(pm): sprint history carries a finish time and title"
```

---

### Task 4: Clip and window the history blocks in the prompt

The dominant growth term. p3 inlines 8,910 B per completed sprint — full original goals plus the full result summary — forever. The PM's session has file tools and runs in the program's workdir, so the full text is one read away; inlining all of it every beat is what makes the prompt grow with the program.

**Files:**
- Modify: `src/coscience/pm_claude.py:22-37` (`render_prompt` block builders)
- Test: `tests/test_pm_claude.py`

**Interfaces:**
- Consumes: `title` / `finished_at` on `completed` and `failed` entries (Task 3).
- Produces: module-level `RECENT_HISTORY: int = 8`, `RESULT_CHARS: int = 800`, `GOAL_CHARS: int = 400` in `pm_claude`. Task 5 imports `RECENT_HISTORY`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pm_claude.py`:

```python
def _done(i, goals="G", result="R"):
    return {"id": f"p1-c{i}-x", "title": f"Sprint {i}", "goals": goals,
            "result": result, "finished_at": float(i)}


def test_recent_results_are_clipped_not_inlined_whole():
    ctx = _ctx()
    ctx.completed = [_done(1, result="R" * 5000)]
    p = render_prompt(ctx)
    assert "R" * 800 in p
    assert "R" * 900 not in p
    assert "clipped" in p              # the PM is told text was withheld, so it can go read it


def test_older_completed_sprints_collapse_to_one_line_but_keep_their_ids():
    ctx = _ctx()
    ctx.completed = [_done(i, goals="G" * 4000, result="R" * 5000) for i in range(20)]
    p = render_prompt(ctx)
    # The oldest survives as an id + title only — ids must stay resolvable for the
    # lineage graph and for release_ids/reopen_ids.
    assert "p1-c0-x" in p
    assert "Sprint 0" in p
    # ...but its bulk is gone, while the newest keeps its (clipped) detail.
    assert p.count("R" * 800) == 8
    assert p.count("G" * 400) == 8


def test_failed_sprints_are_clipped_the_same_way():
    ctx = _ctx()
    ctx.failed = [{"id": "p1-c1-x", "title": "T", "goals": "G" * 4000,
                   "error": "E" * 5000, "finished_at": 1.0}]
    p = render_prompt(ctx)
    assert "E" * 800 in p
    assert "E" * 900 not in p
```

- [ ] **Step 2: Run to verify failure**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_claude.py -v -k "clipped or collapse or failed_sprints"`
Expected: FAIL — the full 5000-char strings are still inlined.

- [ ] **Step 3: Implement the helpers**

In `src/coscience/pm_claude.py`, add below the imports:

```python
# The PM's session has file tools and runs in the program's workdir, so a full
# result is one read away. Inlining every result and every original goal on every
# beat is what made the prompt grow with the program: at ~8.9 KB per completed
# sprint, a program's planner got more expensive the more work it finished.
RECENT_HISTORY = 8      # completed/failed sprints shown with detail
RESULT_CHARS = 800      # per-result / per-error excerpt cap
GOAL_CHARS = 400        # per-history-entry goal excerpt cap


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}… [clipped; {len(text):,} chars — read the file for the rest]"


def _history_block(items: list[dict], recent_fmt) -> str:
    """Recent entries with detail, older ones as one line each. Older entries keep
    their id and title rather than being dropped: the lineage graph and the
    release_ids/reopen_ids instructions both tell the PM to copy ids EXACTLY, so an
    id the prompt never shows is an action it can never take."""
    if not items:
        return "(none)"
    split = max(0, len(items) - RECENT_HISTORY)
    older, recent = items[:split], items[split:]
    lines = [f"- {i['id']}: {i.get('title') or _clip(i.get('goals', ''), 60)}"
             for i in older]
    if older:
        lines.append(f"--- the {len(recent)} most recent, in detail ---")
    lines += [recent_fmt(i) for i in recent]
    return "\n".join(lines)
```

- [ ] **Step 4: Use them in `render_prompt`**

Replace the `done_block` and `failed_block` assignments (`pm_claude.py:28-31`) with:

```python
    done_block = _history_block(
        context.completed,
        lambda s: (f"- {s['id']}: {_clip(s['goals'], GOAL_CHARS)}"
                   f" -> result: {_clip(s['result'], RESULT_CHARS)}"))
    failed_block = _history_block(
        context.failed,
        lambda s: (f"- {s['id']}: {_clip(s['goals'], GOAL_CHARS)}"
                   f" -> FAILED: {_clip(s['error'], RESULT_CHARS)}"))
```

- [ ] **Step 5: Run the tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_claude.py -v`
Expected: PASS. If `test_render_prompt_includes_state_and_json_instruction` fails, check whether its fixture asserts a full result string — clip the expectation, do not raise `RESULT_CHARS`.

- [ ] **Step 6: Measure the real effect**

Run:

```bash
~/venvs/coscience/bin/python - <<'EOF'
import os
os.environ["COSCIENCE_REPO"] = os.path.expanduser("~/sync/bmt-share/coscience")
from coscience.substrate import Substrate
from coscience import pm_agent
from coscience.pm_claude import render_prompt
sub = Substrate(os.environ["COSCIENCE_REPO"])
for p in sub.iter_programs():
    ctx = pm_agent.gather_context(sub, p.id)
    print(f"{p.id}: {len(render_prompt(ctx)):,} B  ({len(ctx.completed)} completed)")
EOF
```

Expected: p3 drops from 129,071 B to roughly 83,000 B. The residue is the idea pool (~28 KB) and open-sprint goals (~21 KB) — both live content the PM is actively deciding on, and both bounded by the sprint cap and pool curation rather than by history. The point of this task is the slope, not the intercept: per-completed-sprint marginal cost falls from ~8,910 B to ~45 B.

- [ ] **Step 7: Commit**

```bash
git add src/coscience/pm_claude.py tests/test_pm_claude.py
git commit -m "feat(pm): clip results and collapse older history in the prompt"
```

---

### Task 5: Window prior proposals, and guard the prompt with a budget test

`pm.proposed_ids` is appended every cycle (`pm_agent.py:846-848`) and never trimmed — `pm.activations` right beside it *is* capped at 50. The full list stays on disk as the audit record; only the rendering is windowed. Then a regression test makes the whole of Phase 2 permanent.

**Files:**
- Modify: `src/coscience/pm_claude.py:57` (`prior_block`)
- Test: `tests/test_pm_claude.py`

**Interfaces:**
- Consumes: `RECENT_HISTORY` (Task 4).
- Produces: module-level `PRIOR_SHOWN: int = 20` in `pm_claude`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pm_claude.py`:

```python
def test_prior_proposals_are_windowed_in_the_prompt():
    ctx = _ctx()
    ctx.prior_proposals = [f"p1-c{i}-x" for i in range(60)]
    p = render_prompt(ctx)
    assert "p1-c59-x" in p            # newest kept
    assert "p1-c0-x" not in p         # oldest dropped from the prompt only
    assert "40 earlier" in p          # the PM is told the list was trimmed


def test_prompt_does_not_grow_with_program_history():
    """The whole point of Phase 2: a program that has finished 100 sprints must not
    cost meaningfully more per beat than one that has finished 20."""
    def ctx_with(n):
        c = _ctx()
        c.completed = [{"id": f"p1-c{i}-x", "title": f"Sprint {i}",
                        "goals": "G" * 4000, "result": "R" * 5000,
                        "finished_at": float(i)} for i in range(n)]
        c.prior_proposals = [f"p1-c{i}-x" for i in range(n)]
        return c

    small = len(render_prompt(ctx_with(20)))
    large = len(render_prompt(ctx_with(100)))
    assert large - small < 8_000, f"prompt grew {large - small:,} B over 80 sprints"
    assert large < 60_000, f"prompt is {large:,} B with 100 sprints of history"
```

- [ ] **Step 2: Run to verify failure**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_claude.py -v -k "windowed or does_not_grow"`
Expected: FAIL — all 60 ids render, and the growth assertion reports roughly 8,000+ B of overshoot from the untrimmed prior list.

- [ ] **Step 3: Implement**

In `src/coscience/pm_claude.py`, add beside the other constants:

```python
PRIOR_SHOWN = 20        # prior proposal ids rendered; the full list stays in pm.md
```

Replace the `prior_block` assignment at line 57 with:

```python
    # pm.proposed_ids is append-only and never trimmed — it is the substrate's audit
    # record and stays complete on disk. Only the rendering is windowed.
    prior = list(context.prior_proposals)
    prior_block = ", ".join(prior[-PRIOR_SHOWN:]) or "(none)"
    if len(prior) > PRIOR_SHOWN:
        prior_block += f" (+{len(prior) - PRIOR_SHOWN} earlier, omitted)"
```

- [ ] **Step 4: Run the full suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_claude.py tests/test_pm_claude.py
git commit -m "feat(pm): window prior proposals and guard the prompt size"
```

---

## Phase 3 — Bound it (Tasks 6–8)

Independent of Phase 2 and mergeable separately. These bound what the loops may spend rather than what one prompt costs.

### Task 6: The usage script path is configurable

`_USAGE_SCRIPT` hardcodes `~/.claude/skills/usage/usage.py` in two modules. It exists on `Avatar`; it is unverified on the live deployment. If it is missing there, `claude_usage_ok` fails open (agents run unmetered) and `read_budget` returns `None` (a blank budget panel) — both silently, on the box where it matters most. `CLAUDE.md` states more than one host may run the full platform.

**Files:**
- Modify: `src/coscience/usage_meter.py:22`, `src/coscience/worker.py` (its `_USAGE_SCRIPT`)
- Test: `tests/test_usage_gate.py`

**Interfaces:**
- Produces: `COSCIENCE_USAGE_SCRIPT` env var overrides the default path in both modules.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_usage_gate.py`:

```python
def test_usage_script_path_is_configurable(monkeypatch, tmp_path):
    import importlib
    from coscience import worker as worker_mod
    fake = tmp_path / "usage.py"
    fake.write_text("print('5h: 3% (resets 1pm)')\n")
    monkeypatch.setenv("COSCIENCE_USAGE_SCRIPT", str(fake))
    importlib.reload(worker_mod)
    try:
        assert worker_mod._USAGE_SCRIPT == str(fake)
        assert worker_mod.claude_usage_ok() is True
    finally:
        monkeypatch.delenv("COSCIENCE_USAGE_SCRIPT")
        importlib.reload(worker_mod)
```

- [ ] **Step 2: Run to verify failure**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_gate.py -v -k configurable`
Expected: FAIL — `_USAGE_SCRIPT` still points at the home-directory default.

- [ ] **Step 3: Implement**

In **both** `src/coscience/usage_meter.py` and `src/coscience/worker.py`, replace the `_USAGE_SCRIPT` assignment with:

```python
# Overridable because it is a personal dotfile: a host without it silently loses
# both the budget panel and the usage gate. See local_setup_*.md per host.
_USAGE_SCRIPT = os.environ.get(
    "COSCIENCE_USAGE_SCRIPT", os.path.expanduser("~/.claude/skills/usage/usage.py"))
```

Both modules already import `os` — no new imports needed.

- [ ] **Step 4: Run the tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Document it**

Add to `local_setup_avatar.md` under **Bringing the platform up**, after the `SUB=` line:

```markdown
`COSCIENCE_USAGE_SCRIPT` defaults to `~/.claude/skills/usage/usage.py`. If a host
lacks it, the usage gate fails open (agents run unmetered) and the dashboard's
budget panel goes blank — set it explicitly on any box where that path differs.
```

- [ ] **Step 6: Commit**

```bash
git add src/coscience/usage_meter.py src/coscience/worker.py tests/test_usage_gate.py local_setup_avatar.md
git commit -m "feat(usage): make the usage-script path configurable per host"
```

---

### Task 7: Reserve usage headroom for human-triggered work

`claude_usage_ok(threshold=100.0)` refuses only when a window is fully exhausted — a smoke alarm that triggers once the house is gone. On a fixed subscription that means an autonomous PM beat can consume the last of the 5-hour window a human wanted for a chat or a forced replan. The usage skill reports percentages, not tokens, so the honest lever is a per-caller threshold, not token arithmetic against an opaque number.

**Files:**
- Modify: `src/coscience/worker.py:71-80` (`claude_usage_ok`)
- Modify: `src/coscience/cli.py:217` (PM loop), `src/coscience/worker.py:185` (worker launch gate)
- Test: `tests/test_usage_gate.py`

**Interfaces:**
- Produces: `AUTONOMOUS_THRESHOLD: float = 80.0` and `WORKER_THRESHOLD: float = 90.0` in `worker.py`; `claude_usage_ok(threshold=100.0, *, fail_open=True)`.
- Human-triggered paths (`service.py:579`, `:593`, `:803`) keep the 100.0 default unchanged — they are the work the reserve exists to protect.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_usage_gate.py`:

```python
def test_autonomous_threshold_reserves_headroom(monkeypatch):
    from coscience import worker as worker_mod
    monkeypatch.setattr(worker_mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": "5h: 85% (resets 1pm)"})())
    # A human-triggered call still gets through at 85% used...
    assert worker_mod.claude_usage_ok() is True
    # ...but an autonomous loop stands down, leaving the rest for the human.
    assert worker_mod.claude_usage_ok(worker_mod.AUTONOMOUS_THRESHOLD) is False


def test_gate_can_fail_closed(monkeypatch):
    from coscience import worker as worker_mod
    def _boom(*a, **k):
        raise OSError("no such script")
    monkeypatch.setattr(worker_mod.subprocess, "run", _boom)
    assert worker_mod.claude_usage_ok() is True                    # default: fail open
    assert worker_mod.claude_usage_ok(fail_open=False) is False    # loops: fail closed
```

- [ ] **Step 2: Run to verify failure**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_gate.py -v -k "headroom or fail_closed"`
Expected: FAIL — `AUTONOMOUS_THRESHOLD` does not exist and `fail_open` is not a parameter.

- [ ] **Step 3: Implement**

In `src/coscience/worker.py`, add above `claude_usage_ok`:

```python
# Usage is a fixed subscription window, not a bill: the scarce thing is the share
# left for a human who wants a chat or a forced replan. Autonomous loops stand down
# early and leave the top band for them; human-triggered paths keep the full 100.
AUTONOMOUS_THRESHOLD = 80.0     # PM loop beats
WORKER_THRESHOLD = 90.0         # worker agent launches
```

and rewrite the function:

```python
def claude_usage_ok(threshold: float = 100.0, *, fail_open: bool = True) -> bool:
    """True if it's safe to launch a Claude agent at this threshold — neither the
    5-hour nor the weekly window has passed it. `fail_open` decides what an
    unreadable usage script means: True for human-triggered work (never block a
    person on a missing dotfile), False for autonomous loops (an unmetered loop is
    exactly what burns a window unattended)."""
    try:
        out = subprocess.run([sys.executable, _USAGE_SCRIPT],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return fail_open
    return _usage_ok_from_output(out, threshold=threshold)
```

`worker.py` does **not** currently import `sys` — add `import sys` to its import block (after `import subprocess`). (`python3` on `PATH` is not necessarily the venv interpreter; `sys.executable` is. Make the same swap in `usage_meter.read_budget`, which shells out the same way.)

Wire the two autonomous callers. In `src/coscience/cli.py:217`:

```python
            summaries = pm_run_once(substrate, reasoner,
                                    usage_ok=lambda: claude_usage_ok(
                                        AUTONOMOUS_THRESHOLD, fail_open=False))
```

and add `AUTONOMOUS_THRESHOLD` to the existing `from coscience.worker import ...` at `cli.py:18`.

In `src/coscience/worker.py:185`:

```python
        return (self._usage_gate or
                (lambda: claude_usage_ok(WORKER_THRESHOLD, fail_open=False)))()
```

- [ ] **Step 4: Run the full suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS. `tests/conftest.py` patches `worker_mod.claude_usage_ok` autouse, so worker tests are unaffected by the new default.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/worker.py src/coscience/cli.py tests/test_usage_gate.py
git commit -m "feat(usage): reserve headroom for human-triggered Claude work"
```

---

### Task 8: A failed cycle backs off instead of retrying every 5 seconds

If `reasoner.run` raises, the exception propagates before `write_staging`, so `pm.last_fingerprint` never advances and `save_pm_state` never runs. `pm_run_once` swallows it and the loop ticks again with identical context. With `--interval` defaulting to 5.0 (`cli.py:151`), a program whose reasoner reliably returns malformed JSON burns a full agentic session every five seconds. Task 2 made this visible in the ledger; this makes it stop.

Also raises the PM loop's default interval: a PM beat takes minutes and the human replan path is a direct call (`service.py:579`), not a poll, so 5-second polling buys nothing and re-walks every `sprint.md` in the substrate each time.

**Files:**
- Modify: `src/coscience/models.py:211-224` (`PMState`), `src/coscience/pm_agent.py` (the `except` from Task 2)
- Modify: `src/coscience/cli.py:151` (PM `--interval` default)
- Test: `tests/test_pm_beat.py`

**Interfaces:**
- Consumes: the `_record(ok=False)` block from Task 2.
- Produces: `PMState.consecutive_failures: int = 0`, and `pm_agent.FAILURE_BACKOFF: int = 3`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pm_beat.py`:

```python
def test_repeated_reasoner_failures_back_off(substrate):
    from coscience.models import Program
    from coscience.pm_claude import PMReasonerError
    from coscience import usage_meter

    substrate.save_program(Program(id="p1", title="P", goals="g"))

    calls = []
    class Boom:
        last_cost = None
        last_prompt_bytes = 10
        def run(self, ctx):
            calls.append(1)
            raise PMReasonerError("bad json")

    boom = Boom()
    for _ in range(6):
        try:
            pm_beat(substrate, "p1", boom)
        except PMReasonerError:
            pass

    # Three attempts against unchanged context, then it stands down rather than
    # burning a full agentic session every beat.
    assert len(calls) == 3
    assert substrate.load_pm_state("p1").consecutive_failures == 3
    assert len(usage_meter.load_runs(substrate.repo_root)) == 3


def test_a_changed_context_clears_the_backoff(substrate):
    from coscience.models import Program, Sprint, SprintStatus
    from coscience.pm_claude import PMReasonerError

    substrate.save_program(Program(id="p1", title="P", goals="g"))

    class Boom:
        last_cost = None
        last_prompt_bytes = 10
        def run(self, ctx):
            raise PMReasonerError("bad json")

    for _ in range(4):
        try:
            pm_beat(substrate, "p1", Boom())
        except PMReasonerError:
            pass
    assert substrate.load_pm_state("p1").consecutive_failures == 3

    # A human doing something — approving a sprint here — is new information, so the
    # PM must try again rather than stay stuck behind a stale failure count.
    substrate.save_sprint(Sprint(id="p1-a", status=SprintStatus.APPROVED, goals="g",
                                 plan=["x"], program="p1"))
    try:
        pm_beat(substrate, "p1", Boom())
    except PMReasonerError:
        pass
    assert substrate.load_pm_state("p1").consecutive_failures == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pm_beat.py -v -k backoff`
Expected: FAIL — six calls are made, and `PMState` has no `consecutive_failures`.

- [ ] **Step 3: Implement the state field**

In `src/coscience/models.py`, add to `PMState` after `last_fingerprint`:

```python
    # Consecutive reasoner failures against THIS fingerprint. A cycle that raises
    # (malformed JSON is the common one) has already spent its tokens, and the
    # fingerprint gate cannot help: the context is unchanged, so the next beat would
    # retry identically — every interval, forever.
    consecutive_failures: int = 0
    failed_fingerprint: str = ""
```

`save_pm_state` / `load_pm_state` (`substrate.py:363-395`) enumerate fields explicitly — a new dataclass field is **not** persisted automatically, and without this the backoff counter resets to 0 on every beat and the test in Step 1 fails on the fourth call. Add to `load_pm_state`'s `PMState(...)` construction:

```python
            consecutive_failures=int(fm.get("consecutive_failures", 0)),
            failed_fingerprint=str(fm.get("failed_fingerprint", "")),
```

and to `save_pm_state`, beside the other conditional keys (omitted when zero/empty so a healthy program's `pm.md` is unchanged):

```python
        if state.consecutive_failures:
            fm["consecutive_failures"] = state.consecutive_failures
        if state.failed_fingerprint:
            fm["failed_fingerprint"] = state.failed_fingerprint
```

- [ ] **Step 4: Implement the backoff**

In `src/coscience/pm_agent.py`, add beside `MAX_PROPOSED`:

```python
# Attempts against one unchanged context before the PM stands down. Bounded, not
# zero: a flaky call deserves a retry, a deterministic one does not deserve 720
# per hour.
FAILURE_BACKOFF = 3
```

In `_run_pm_cycle`, immediately after the `usage_ok` check and before `new_signals = context_signals(context)`:

```python
        if (fingerprint == pm.failed_fingerprint
                and pm.consecutive_failures >= FAILURE_BACKOFF):
            # Same context, already failed FAILURE_BACKOFF times — retrying spends a
            # full agentic session for the same raise. Wait for something to change.
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            return {"program": program_id, "cycle": cycle, "submitted": [],
                    "proposed": [], "skipped": True, "backoff": True}
```

Extend the `except` block written in Task 2:

```python
        except Exception:
            _record(ok=False)
            # Count it against THIS context: new information resets the counter, so a
            # human approving something always gets a fresh attempt.
            pm.consecutive_failures = (pm.consecutive_failures + 1
                                       if fingerprint == pm.failed_fingerprint else 1)
            pm.failed_fingerprint = fingerprint
            pm.last_run = time.time() if now is None else now
            substrate.save_pm_state(pm)
            raise
```

And clear it on success — in the `if new_signals is not None:` block near the end, before `pm.activations.append(...)`:

```python
        pm.consecutive_failures = 0
        pm.failed_fingerprint = ""
```

- [ ] **Step 5: Raise the PM loop interval**

In `src/coscience/cli.py:151`, change the PM parser's default:

```python
    pm.add_argument("--interval", type=float, default=60.0)
```

Leave the worker and dispatch defaults at 5.0 — the dispatcher must stay responsive for lease grants. A PM beat takes minutes, and a human "replan now" is a direct `pm_beat(force=True)` call from `service.py:579`, never a poll, so nothing gets slower for a person.

- [ ] **Step 6: Run the full suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/coscience/models.py src/coscience/pm_agent.py src/coscience/cli.py tests/test_pm_beat.py
git commit -m "fix(pm): back off after repeated reasoner failures; slow the PM poll"
```

---

## Deliberately not in this plan

- **A dollar cap per call (`--max-budget-usd`).** It is a dollar cap; on a fixed subscription that is the wrong unit. Task 7 is the subscription-native equivalent.
- **An mtime cache to skip `gather_context` on idle beats.** The payoff is disk I/O; the failure mode is a PM that silently goes deaf to a change the cache key missed. Task 8's interval change gets a 12× reduction with no such risk. Revisit only if profiling shows the walk actually hurts.
- **A usage dashboard page or alerting.** There are 80 records in `runs.jsonl` today. Land Tasks 1–2, let a month accumulate with the factors recorded, then decide what is worth rendering.
- **Clipping the idea pool or open-sprint goals.** After Task 4 these are p3's largest blocks (~28 KB and ~21 KB), but both are live content the PM is actively deciding on, and both are already bounded — by the sprint cap and by pool curation. Neither grows with program history, which is the problem this plan addresses.

## Verification before calling it done

- [ ] `~/venvs/coscience/bin/python -m pytest` — expect 823 baseline + ~14 new, zero failures.
- [ ] `cd frontend && npm test` — expect 97 passing, untouched by this plan.
- [ ] Re-run the Task 4 Step 6 measurement script; record the before/after per program in the merge commit.
- [ ] Confirm no program's fingerprint changed: for each of p1–p4, `context_fingerprint(gather_context(sub, pid))` must equal the `last_fingerprint` stored in that program's `pm.md`. A mismatch means a context-side change slipped in and every program will re-reason once on deploy.
