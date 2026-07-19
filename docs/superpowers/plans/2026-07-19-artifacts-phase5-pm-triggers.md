# Artifacts Phase 5 — PM Triggers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A comment on an artifact reaches the PM, which (propose-only) can emit an
`artifact_task` → a **proposed** sprint bound to that artifact (or creating a new
one), and reply on the comment thread. Covers "comment → PM updates the artifact"
and "ask the PM to create a new artifact."

**Architecture:** `PMContext` gains the artifact list + open artifact-comment
threads; `PMCycleOutput` gains `artifact_tasks`. The reasoner prompt describes both
and the new output; `parse_response` reads it. `pm_agent` feeds artifacts into the
context (a new `artifact_feedback` payload category re-triggers the PM), round-trips
`artifact_tasks` through staging, and — in the idempotent apply — turns each
`artifact_task` into a PROPOSED sprint with `artifacts_bound`/`artifacts_create`
set, and posts the PM's reply on the originating artifact thread.

**Tech Stack:** the existing PM machinery (`pm_reasoner.py`, `pm_claude.py`,
`pm_agent.py`), `coscience.threads`, `pytest` with `substrate` + `FakeReasoner`.

## Global Constraints

- **The PM stays propose-only** — it never edits artifacts. An `artifact_task`
  becomes a **PROPOSED** sprint (human-gated, like every other PM proposal), which
  a worker later runs (Phase 2 machinery). It counts against the `MAX_PROPOSED` cap.
- **Artifact comment threads are always `target:"pm"`** (Phase 3a) — they enter the
  PM context and the PM answers via the existing `thread_replies` mechanism.
- **`artifact_task` shape:** `{suffix, artifact_ids: [str], create: [{title, kind}], instructions}`
  → sprint `artifacts_bound = artifact_ids`, `artifacts_create = [{aid, title, kind}]`
  (aid = a deterministic slug of the title), `goals = instructions`.
- Additions are **additive/back-compat**: new dataclass fields default empty; a
  program with no artifacts feeds empty blocks and does not change PM behavior.
- Deterministic + idempotent apply (re-applying a staged cycle must not duplicate
  sprints), matching the existing `_run_pm_cycle` discipline.

**Base commit for this phase:** current `feat/artifacts` HEAD (Phase 4 complete).

---

### Task 1: Reasoner — context fields, output field, prompt + parse

**Files:**
- Modify: `src/coscience/pm_reasoner.py` (`PMContext`, `PMCycleOutput`)
- Modify: `src/coscience/pm_claude.py` (`render_prompt`, `parse_response`)
- Test: `tests/test_pm_artifact_reasoner.py`

**Interfaces:**
- Produces:
  - `PMContext.artifacts: list[dict]` (each `{id, title, kind}`), `PMContext.artifact_feedback: list[dict]` (each `{artifact_id, thread_id, messages:[{role,text}]}`).
  - `PMCycleOutput.artifact_tasks: list[dict]`.
  - `render_prompt` emits an `ARTIFACTS` block, an `ARTIFACT FEEDBACK` block, and an `artifact_tasks` entry in the JSON schema.
  - `parse_response` reads `data.get("artifact_tasks", [])` into `PMCycleOutput.artifact_tasks` (keeping only dicts).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pm_artifact_reasoner.py
from coscience.pm_claude import parse_response, render_prompt
from coscience.pm_reasoner import PMContext


def _ctx(**kw):
    return PMContext(program_id="p", goals="g", cycle=0, **kw)


def test_render_prompt_lists_artifacts_and_feedback():
    ctx = _ctx(
        artifacts=[{"id": "manuscript", "title": "Manuscript", "kind": "md"}],
        artifact_feedback=[{"artifact_id": "manuscript", "thread_id": "t1",
                            "messages": [{"role": "human", "text": "tighten the intro"}]}])
    p = render_prompt(ctx)
    assert "ARTIFACTS" in p
    assert "manuscript" in p
    assert "tighten the intro" in p
    assert "artifact_tasks" in p


def test_parse_reads_artifact_tasks():
    out = parse_response(
        '{"report":"r","artifact_tasks":[{"suffix":"fix-intro","artifact_ids":["manuscript"],'
        '"create":[],"instructions":"tighten the introduction per the comment"}]}')
    assert len(out.artifact_tasks) == 1
    assert out.artifact_tasks[0]["artifact_ids"] == ["manuscript"]


def test_parse_artifact_tasks_defaults_empty():
    out = parse_response('{"report":"r"}')
    assert out.artifact_tasks == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_pm_artifact_reasoner.py -v`
Expected: FAIL (`PMContext` has no `artifacts` / `render_prompt` lacks the block).

- [ ] **Step 3: Implement**

In `src/coscience/pm_reasoner.py`, add to `PMContext` (after `graph_lines`):

```python
    artifacts: list[dict] = field(default_factory=list)          # program artifacts: [{id, title, kind}]
    artifact_feedback: list[dict] = field(default_factory=list)  # open human-last threads on artifacts (target "pm")
```

and to `PMCycleOutput` (after `edge_ops`):

```python
    artifact_tasks: list[dict] = field(default_factory=list)  # [{suffix, artifact_ids, create:[{title,kind}], instructions}]
```

In `src/coscience/pm_claude.py` `render_prompt`, build two blocks (place the
definitions near the other `_lines(...)` blocks, before the `return`):

```python
    artifacts_block = _lines(context.artifacts,
                             lambda a: f"- [{a['id']}] {a.get('title', a['id'])} ({a.get('kind', 'md')})")

    def _artifact_feedback_line(f):
        history = " | ".join(f"{m['role']}: {m['text']}" for m in f["messages"])
        return f"- artifact [{f['artifact_id']}], thread {f['thread_id']}: {history}"
    artifact_feedback_block = _lines(context.artifact_feedback, _artifact_feedback_line)
```

Insert this section into the prompt text (e.g. right after the `IDEA FEEDBACK`
block, before `GUIDANCE FEEDBACK`):

```python
    # (interpolated into the f-string below)
```

Add to the returned f-string, after the idea-feedback block:

```
ARTIFACTS (the program's deliverables — reports, data, figures, pages — that agents produce and evolve):
{artifacts_block}

HUMAN FEEDBACK ADDRESSED TO YOU about specific artifacts — same thread_replies mechanism.
For each open thread: decide the right action and, when it needs work on the artifact,
emit an artifact_task (below) that proposes a sprint to do it; then add a thread_replies
entry with that artifact thread's id saying what you proposed (or why not).
ARTIFACT FEEDBACK:
{artifact_feedback_block}
```

Add an `artifact_tasks` entry to the JSON-shape section (after `edge_ops`):

```
  "artifact_tasks": [
    {{"suffix": "<short-slug naming the update>",
      "artifact_ids": ["<existing artifact id(s) this sprint will edit>", "..."],
      "create": [{{"title": "<new artifact to create>", "kind": "md|data|figure|page"}}],
      "instructions": "<what the sprint should do to the artifact(s) — becomes the sprint's goals>"}}
  ],
```

and a short guidance bullet in the prose (near the "MANAGE THE APPROVED QUEUE" bullet):

```
- ARTIFACTS: when a human comment asks for work on an artifact, or asks for a new
  artifact, propose it as an artifact_task (it becomes a PROPOSED sprint bound to
  that artifact — humans approve it like any sprint; it counts against the cap). Bind
  existing artifacts with artifact_ids; declare new ones in create. Do NOT try to edit
  artifacts yourself — you only propose.
```

In `src/coscience/pm_claude.py` `parse_response`, add to the `PMCycleOutput(...)`
constructor:

```python
        artifact_tasks=[dict(t) for t in data.get("artifact_tasks", []) if isinstance(t, dict)],
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_pm_artifact_reasoner.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the existing PM reasoner tests (regression) + commit**

Run: `python -m pytest tests/test_pm_claude.py tests/test_pm_reasoner.py -v`
Expected: PASS (existing render/parse tests still green — additions are additive).

```bash
git add src/coscience/pm_reasoner.py src/coscience/pm_claude.py tests/test_pm_artifact_reasoner.py
git commit -m "feat(artifacts): PM context artifacts + artifact_tasks output + prompt"
```

---

### Task 2: gather_context feeds artifacts + re-triggers on artifact comments

**Files:**
- Modify: `src/coscience/pm_agent.py` (`_context_payload`, `_TRIGGER_LABELS`, `gather_context`)
- Test: `tests/test_pm_artifact_context.py`

**Interfaces:**
- Consumes: `Substrate.iter_artifacts/load_artifact`, `threads.needs_reply`.
- Produces: `gather_context` populates `PMContext.artifacts` (id/title/kind of every
  non-archived artifact) and `PMContext.artifact_feedback` (open human-last threads
  on artifacts); a new artifact comment changes `context_fingerprint` (re-triggers
  the PM), labeled "comment on an artifact".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pm_artifact_context.py
from coscience import artifacts, threads
from coscience.models import Program
from coscience.pm_agent import context_fingerprint, gather_context


def _program(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))


def test_gather_lists_artifacts(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "figure")
    ctx = gather_context(substrate, "p")
    assert ctx.artifacts == [{"id": "doc", "title": "Doc", "kind": "figure"}]


def test_open_artifact_comment_is_feedback_and_retriggers(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    before = context_fingerprint(gather_context(substrate, "p"))
    # add a human comment thread (target pm) on the artifact
    a = substrate.load_artifact("p", "doc")
    a.threads.append(threads.new_thread("pm", "please tighten", by="oleg", now=1.0))
    substrate.save_artifact(a)
    ctx = gather_context(substrate, "p")
    assert len(ctx.artifact_feedback) == 1
    assert ctx.artifact_feedback[0]["artifact_id"] == "doc"
    assert context_fingerprint(ctx) != before          # a new comment re-triggers the PM
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_pm_artifact_context.py -v`
Expected: FAIL (`ctx.artifacts` empty / attribute missing).

- [ ] **Step 3: Implement**

In `src/coscience/pm_agent.py` `_context_payload`, add an `artifact_feedback`
entry to the returned dict (same shape as `idea_comments`):

```python
        "artifact_feedback": sorted((f["artifact_id"], f["thread_id"], f["messages"][-1]["text"])
                                    for f in context.artifact_feedback),
```

Add to `_TRIGGER_LABELS`:

```python
    "artifact_feedback": "comment on an artifact",
```

In `gather_context`, after the ideas/idea_feedback gathering and before building the
graph window, collect artifacts + their open pm-threads:

```python
    artifact_dicts: list[dict] = []
    artifact_feedback: list[dict] = []
    for art in substrate.iter_artifacts(program_id):
        artifact_dicts.append({"id": art.id, "title": art.title, "kind": art.kind})
        for th in art.threads:
            if threads.needs_reply(th):
                artifact_feedback.append({
                    "artifact_id": art.id, "thread_id": th["id"],
                    "messages": [{"role": m["role"], "text": m["text"]} for m in th["messages"]],
                })
```

and pass them into the `PMContext(...)` constructor (add the two arguments):

```python
        artifacts=artifact_dicts, artifact_feedback=artifact_feedback,
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_pm_artifact_context.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing PM agent tests (regression) + commit**

Run: `python -m pytest tests/test_pm_agent.py -v`
Expected: PASS (event-gate/fingerprint tests still green — the new empty category is stable).

```bash
git add src/coscience/pm_agent.py tests/test_pm_artifact_context.py
git commit -m "feat(artifacts): PM gathers artifacts + re-triggers on artifact comments"
```

---

### Task 3: Round-trip `artifact_tasks` through staging

**Files:**
- Modify: `src/coscience/pm_agent.py` (`write_staging`, `read_staging`)
- Test: `tests/test_pm_artifact_staging.py`

**Interfaces:**
- Produces: `write_staging` persists `output.artifact_tasks`; `read_staging`
  restores them into the reconstructed `PMCycleOutput` — so a kill between reasoning
  and apply replays the same artifact tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pm_artifact_staging.py
from coscience.models import Program
from coscience.pm_agent import read_staging, write_staging
from coscience.pm_reasoner import PMCycleOutput


def test_artifact_tasks_survive_staging(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "fix", "artifact_ids": ["doc"], "create": [], "instructions": "tighten"}])
    write_staging(substrate, "p", 3, out)
    staged = read_staging(substrate, "p")
    assert staged.output.artifact_tasks == [
        {"suffix": "fix", "artifact_ids": ["doc"], "create": [], "instructions": "tighten"}]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_pm_artifact_staging.py -v`
Expected: FAIL (`staged.output.artifact_tasks` is `[]`).

- [ ] **Step 3: Implement**

In `src/coscience/pm_agent.py` `write_staging`, add to the `data` dict (after `"edge_ops": ...`):

```python
        "artifact_tasks": list(output.artifact_tasks),
```

In `read_staging`, add to the `PMCycleOutput(...)` reconstruction (after `edge_ops=...`):

```python
        artifact_tasks=list(data.get("artifact_tasks", [])),
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_pm_artifact_staging.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_pm_artifact_staging.py
git commit -m "feat(artifacts): round-trip artifact_tasks through PM staging"
```

---

### Task 4: Apply `artifact_tasks` → proposed sprint + artifact thread replies

**Files:**
- Modify: `src/coscience/pm_agent.py` (`_run_pm_cycle` — apply loop; the `thread_replies` application)
- Test: `tests/test_pm_artifact_apply.py`

**Interfaces:**
- Consumes: `proposal_id` (existing), `Sprint`, `SprintStatus`, `threads`.
- Produces:
  - Each `artifact_task` becomes a PROPOSED `Sprint` with `artifacts_bound` = its
    `artifact_ids`, `artifacts_create` = `[{aid: slug(title), title, kind}]`, and
    `goals` = its `instructions`; created idempotently (skip if the id already
    exists), counting against the same free-slot budget as proposals.
  - The `thread_replies` application also reaches **artifact** threads (so the PM's
    reply lands on an artifact comment).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pm_artifact_apply.py
from coscience import artifacts, threads
from coscience.models import Program, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _program(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))


def test_artifact_task_becomes_proposed_bound_sprint(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "tighten-intro", "artifact_ids": ["doc"], "create": [],
         "instructions": "Tighten the introduction."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    sid = "p-c0-tighten-intro"
    s = substrate.load_sprint(sid)
    assert s.status == SprintStatus.PROPOSED
    assert s.artifacts_bound == ["doc"]
    assert "Tighten" in s.goals


def test_artifact_task_create_new_artifact_sprint(substrate):
    _program(substrate)
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "write-manuscript", "artifact_ids": [],
         "create": [{"title": "Manuscript", "kind": "md"}],
         "instructions": "Write a manuscript from the results."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    s = substrate.load_sprint("p-c0-write-manuscript")
    assert s.artifacts_create and s.artifacts_create[0]["title"] == "Manuscript"
    assert s.artifacts_create[0]["kind"] == "md"
    assert s.artifacts_create[0]["aid"]      # a slug was assigned


def test_pm_reply_lands_on_artifact_thread(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    a = substrate.load_artifact("p", "doc")
    th = threads.new_thread("pm", "please tighten", by="oleg", now=1.0)
    a.threads.append(th)
    substrate.save_artifact(a)
    out = PMCycleOutput(report="r",
                        thread_replies=[{"thread_id": th["id"], "text": "Proposed a sprint to do it."}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=2.0, force=True)
    a2 = substrate.load_artifact("p", "doc")
    msgs = a2.threads[0]["messages"]
    assert msgs[-1]["role"] == "pm"
    assert "Proposed a sprint" in msgs[-1]["text"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_pm_artifact_apply.py -v`
Expected: FAIL (no sprint created from the artifact_task; artifact thread reply not applied).

- [ ] **Step 3: Implement**

In `src/coscience/pm_agent.py` `_run_pm_cycle`, add a slug helper at module scope
(near `proposal_id`):

```python
def _artifact_slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    return s or "artifact"
```

Apply the artifact tasks right after the proposals loop (so they share the same
`slots` budget). Insert after the proposals `for prop in staged.output.proposals:`
loop completes:

```python
    for task in staged.output.artifact_tasks:
        if not isinstance(task, dict):
            continue
        suffix = str(task.get("suffix") or "artifact-update")
        sid = proposal_id(program_id, cycle, suffix)
        if (substrate.sprint_dir(sid) / "sprint.md").is_file():
            if sid not in proposed:
                proposed.append(sid)
            continue
        if slots <= 0:
            dropped.append(sid)
            continue
        bound = [str(a) for a in task.get("artifact_ids", []) if str(a).strip()]
        create = []
        for c in task.get("create", []):
            if isinstance(c, dict) and str(c.get("title") or "").strip():
                title = str(c["title"])
                create.append({"aid": _artifact_slug(title), "title": title,
                               "kind": str(c.get("kind") or "md")})
        if not bound and not create:
            continue                                   # nothing to act on
        substrate.save_sprint(Sprint(
            id=sid, status=SprintStatus.PROPOSED,
            goals=str(task.get("instructions") or "Update the artifact."),
            plan=[], program=program_id,
            artifacts_bound=bound, artifacts_create=create))
        slots -= 1
        proposed.append(sid)
        if sid not in pm.proposed_ids:
            submitted.append(sid)
```

Extend the `thread_replies` application (the block that loops sprints, then ideas,
then guidance) with an artifacts pass. After the guidance-threads reply block, add:

```python
        touched_art = False
        for art in substrate.iter_artifacts(program_id):
            hit = False
            for th in art.threads:
                if th["id"] in replies and threads.needs_reply(th):
                    threads.append(th, "pm", replies[th["id"]], "", now=now_ts)
                    hit = True
            if hit:
                substrate.save_artifact(art)
                touched_art = True
        _ = touched_art
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_pm_artifact_apply.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full PM suite + commit**

Run: `python -m pytest tests/test_pm_agent.py tests/test_pm_artifact_apply.py tests/test_pm_artifact_context.py tests/test_pm_artifact_staging.py -v`
Expected: PASS (all green — existing PM apply behavior unaffected).

```bash
git add src/coscience/pm_agent.py tests/test_pm_artifact_apply.py
git commit -m "feat(artifacts): PM applies artifact_tasks -> proposed sprint + artifact replies"
```

---

## Phase 5 Done — What Exists Now

A human comment on an artifact re-triggers the PM; the PM (still propose-only) can
emit an `artifact_task` that becomes a **proposed** sprint bound to the artifact (or
creating a new one) and replies on the comment thread. "Comment → PM proposes a fix"
and "ask the PM to create a new artifact" both work end-to-end, human-gated.

**The artifacts feature (Phases 1–5) is complete.** Separate follow-up (own spec):
the Claude-artifacts linking research spike. Deferred nits recorded in the SDD
ledger (figure/page live-preview in chat; reaped-chat UI state; version-count
display parity; two artifact_tasks creating the same-slug aid).
