# Program Wiki — Design (the "Knowledge" subsystem)

**Status:** design approved in chat, pending spec review
**Date:** 2026-08-20
**Branch:** `feat/program-wiki`
**Scope:** a per-program, agent-maintained knowledge base built from the platform's
own output, its concept graph, and the UI to explore it.
**Companion document:** `docs/knowledge-charter.md` — the pick-up-here brief for
another agent or another machine. This spec is the *what and why*; the charter is
the *where we are*.

---

## 1. Summary

Every sprint result and every artifact version this platform produces is, today,
a leaf. It is read once by the PM (clipped to 800 characters), cited once in a
report, and then it sits in `results/` forever. Nothing accumulates. A program
that has run sixty sprints does not know more than the sum of its last eight
result summaries, because that is all its planner can hold.

The Program Wiki fixes that by adding a compiled knowledge layer between raw
output and everyone who reads it. When a sprint finishes or an artifact version
is cut, an agent reads it, extracts the concepts and entities it contains,
merges them into existing pages, records typed relations between them, and cites
the claims back to the object that produced them. Every few ingests the wiki is
linted: broken links, contradictions, stale claims, orphans and duplicates get
found and fixed.

The result is a **compounding artifact**. The cross-references are already there;
the synthesis already reflects everything the program has done. Questions get
answered from the wiki rather than re-derived from the raw pile.

**The three principles this design holds to:**

- **The bundle is the product.** Everything durable is markdown-with-frontmatter
  in the substrate git repo, conformant to an open standard, readable without
  this platform. Caches and run state are derived and disposable.
- **Nothing is duplicated.** `results/` and artifact versions already are the
  immutable raw layer. The wiki points at them and hashes them; it never copies
  them.
- **Provenance is not optional.** A claim without a source is a bug. Every page
  names the coscience objects it came from; every substantive claim footnotes
  which one.

---

## 2. Background

### 2.1 The LLM-wiki pattern

Andrej Karpathy's [llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
(early 2026) argues against live RAG over raw documents. Retrieval re-derives an
answer from scratch every time; nothing accumulates. Instead: compile sources
**once** into a persistent interlinked markdown wiki, and query the wiki. Three
layers — immutable raw sources, LLM-written wiki pages, and a schema document
defining the conventions — and four operations: **ingest**, **query**,
**research**, **lint**.

The pattern's insight is a division of labour. Humans curate sources and ask good
questions. The maintenance that defeated Vannevar Bush's Memex — updating
cross-references, keeping summaries current, noting contradictions — falls to a
model that does not get bored and can touch forty files in one pass.

### 2.2 The reference implementation we are porting from

`docs/_tmp_wiki/llm-wiki-skills/` (in this repo, untracked) is an opinionated
instance of the pattern, and it is the most valuable single input to this design.
Its hard-won rules are adopted here as normative:

- **Source pages ground; they are not graph nodes.** A source page carries
  `graph_excluded: true`. Concepts are extracted *from* a source. The source's
  title never becomes a concept — a paper about quantitative investing yields
  `mean-reversion` and `kelly-criterion`, never `quantitative-investing`.
- **Merge-first.** Never create a duplicate, never delete. Search by slug and by
  `aliases` first; when in doubt, update the closest match.
- **Over-extract.** A source naming twenty distinct things produces ~twenty
  pages, not three umbrella summaries. A small redundant page costs nothing; a
  missing node is a hole in the graph.
- **The four-pass deep protocol** — structure map, claim-level extraction,
  **relationship triples over a fixed vocabulary**, cross-wiki contradiction
  scan. The triples pass is what makes this a knowledge graph rather than a pile
  of linked notes, so it is the *standard* ingest path here, not an opt-in mode.
- **Preserve layered insight.** Surface intuition, sharper statement, structural
  implication and downstream connection are four things, not one bullet.

Its `lint.py` (frontmatter validity, broken links, stubs, staleness, source-hash
drift, duplicate stems) is the seed of §9.

### 2.3 Open Knowledge Format

Google Cloud published [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
on 2026-06-12, now at **v0.2**, explicitly as *"an open specification that
formalizes the LLM-wiki pattern into a portable, interoperable format."* It is
the pattern above, standardized: a directory of markdown files with YAML
frontmatter, cross-linked into a graph, with no registry, SDK, or vendor account
required to read or write it. Reference tooling is Apache-2.0.

Conformance is deliberately permissive. A bundle conforms if every non-reserved
`.md` file has parseable frontmatter containing a non-empty `type`, and the
reserved files (`index.md`, `log.md`) follow their defined shapes. Consumers must
tolerate unknown types, unknown frontmatter keys, and broken links.

**We adopt OKF v0.2 as the wiki's on-disk format.** Three reasons:

1. It is the same design we had already converged on, so conformance is nearly
   free, and it brings four families we would otherwise have invented worse
   versions of (§6): `sources` with per-claim footnote attribution, the
   `generated` / `verified` trust family, `status` lifecycle, and `stale_after`.
2. It makes `programs/<pid>/wiki/` portable. Obsidian, GitBook, any OKF consumer,
   or a future non-Claude agent can read it with no work from us.
3. This project already intended it. `substrate.py`'s docstring opens *"Read/write
   the OKF substrate"*; `docs/initial_specs.md:26` recommends OKF for the results
   database; and the original platform design
   (`2026-06-23-co-science-platform-design.md` §2) specifies "Content → a git repo
   (OKF-formatted markdown)". Adopting it here closes that loop and sets the
   precedent for making `results/` conformant later.

**Where we extend OKF:** typed `relations` (§7). OKF explicitly declines to define
a relation taxonomy and requires consumers to tolerate unknown keys, so this is a
legal extension rather than a fork. It is documented as a co-science extension in
the bundle's own `CLAUDE.md` so a foreign consumer knows to ignore it.

**Where OKF differs from the Obsidian tradition:** cross-links are markdown links
(`[text](/concepts/foo.md)`), not `[[wikilinks]]`. Obsidian resolves markdown
links and graphs them normally, so this costs nothing, and lint mechanically
rewrites `[[slug]]` into the OKF form when an agent slips (§9).

---

## 3. Decisions taken before this spec

Recorded so a later reader does not relitigate them.

| Decision | Choice | Why |
|---|---|---|
| Corpus | Sprint results + artifact versions | The platform's own output; the compounding loop. External literature is designed for and deferred (§14). |
| Who ingests | A background maintenance beat | No compute grant, no approval queue, no human in the path. |
| Human role | Read + curate | Browse, search, graph; mark verified, set status, write protected notes, delete. Prose is written by agents. |
| First milestone | Ingest + browse | Proves the loop end to end before investing in visualization. |
| Graph construction | **Approach C** — markdown links as substrate, typed relations as overlay, lint enforcing containment | Untyped links alone cannot answer "what contradicts X"; typed frontmatter alone drifts from the prose. The containment invariant is what stops the drift. |
| On-disk format | OKF v0.2 + a documented `relations` extension | §2.3. |

**Approach C, stated as an invariant:** *every typed relation on a page must also
appear as a markdown link in that page's body.* Lint enforces it (`rel/no-link`).
The graph view toggles between all links and typed-only.

---

## 4. Storage layout

```
programs/<pid>/
  wiki/                       ← the OKF bundle. Portable. This is the product.
    index.md                  reserved; okf_version: "0.2" in its frontmatter
    log.md                    reserved; append-only chronology, newest first
    CLAUDE.md                 the schema layer — conventions for agents working here
    QUESTIONS.md              open / resolved questions
    concepts/<slug>.md
    entities/<slug>.md
    syntheses/<slug>.md
    sources/<slug>.md         grounding pages; graph_excluded: true
  .wiki/                      ← machine state. Derived, disposable, NOT in the bundle.
    state.json
    graph.json                cache; rebuildable from the bundle
    lint/<YYYY-MM-DD>.md      lint reports
    runs/<run-id>/            instructions.md · agent.out · agent.exit · scratchpad.md · report.json
```

`.wiki/` is a **sibling** of `wiki/`, not a child, so the bundle stays a clean
portable OKF directory: copy `wiki/` anywhere and it is valid with nothing to
strip. It sits beside the existing `.pm/` for consistency.

The bundle lives in the substrate git repo, so page history, blame and diffs come
free from `substrate.commit()`. **Obsidian opens `programs/<pid>/wiki` as a vault
directly** — that is the Obsidian experience, at a cost of zero lines of code,
while our own three-pane UI (§11) is the in-dashboard view.

`CLAUDE.md` inside the bundle is picked up automatically by `claude` when a run's
cwd is the wiki directory. That is Karpathy's schema layer, obtained for free.

---

## 5. The source layer: pointing, not copying

`results/*.md` and `programs/<pid>/artifacts/<aid>/<vid>/` already are the
immutable raw layer, already content-addressed by git. The wiki does not copy
them. A `sources/` page is a **grounding page**: a short summary plus a pointer
and a hash.

### 5.1 Object identity

Two co-science object kinds are ingestable in this design:

| Kind | Object id | Content hash |
|---|---|---|
| Sprint result | `result:<result_id>` | `sha256` of the result `.md` bytes |
| Artifact version | `artifact:<aid>@<vid>` | directory digest: `sha256` over the sorted list of `(relpath, sha256(bytes))` for every file in the version directory |

Both are deterministic and stable. The hash is what makes re-ingest correct: if
a result file is edited after ingest, its hash changes, it reappears as pending,
and its source page is refreshed rather than silently going stale.

### 5.2 Which objects belong to a program

- **Results:** `Result.sprint` → `Substrate.load_sprint(sprint_id).program`.
  Results whose sprint is missing or belongs to another program are skipped.
- **Artifacts:** every non-archived artifact's **current** version only, as
  `artifact:<aid>@<current>`. When `current` moves from `v2` to `v3`,
  `artifact:<aid>@v3` becomes a new pending object; `@v2` stays recorded as
  already-ingested history. Intermediate versions are never ingested, so a figure
  revised five times produces one page trail rather than five near-identical
  source pages.

### 5.3 Ordering

Pending objects are ingested **oldest first**, by `Result.completed_at` /
`ArtifactVersion.created_at`. Two consequences: `log.md` becomes a true
chronology of the program, and later ingests can reference concepts that earlier
ones created — which is the whole point of a compounding wiki.

### 5.4 Source page shape

Slugs for source pages are **assigned by the platform, not the agent**, so the
platform can always find an object's source page without searching:

- `sources/result-<result_id>.md`
- `sources/artifact-<aid>-<vid>.md`

```yaml
---
type: Source
title: "Sprint p3-c14 — beat record 1572 (GPU)"
description: One-line statement of what this object established.
resource: /results/p3-c14-beat-record-1572-gpu-result.md   # OKF: canonical asset URI
tags: [sprint-result, gpu, sieve]
generated: { by: coscience-wiki/claude-sonnet-5, at: 2026-08-20T11:04:00Z }
origin: result:p3-c14-beat-record-1572-gpu-result           # extension: stable coscience id
origin_hash: "sha256:1f3c…"                                 # extension: drift detection
graph_excluded: true                                        # extension: grounds, not a node
---

# Summary
# Section map
# Notable insights
# Concepts extracted
```

`resource` is the OKF-portable pointer (substrate-relative). `origin` is the
stable id used for the reverse index — it is what lets SprintDetail ask "which
concepts did this sprint produce?" (§11, phase 4). `origin_hash` is precomputed
**by the platform and handed to the agent in the prompt**; the agent must never
compute it, because a hash it invents is a hash that cannot detect drift.

---

## 6. Page schema

Concept, Entity and Synthesis pages share one schema. This is the normative
template; it is reproduced in the bundle's `CLAUDE.md`.

```yaml
---
type: Concept                    # OKF-required. Concept | Entity | Synthesis | Source | Question
title: Template replication takeoff
description: The regime where template-directed replication outruns hydrolysis.
tags: [abiogenesis, replication, kinetics]
status: draft                    # OKF lifecycle: draft | stable | deprecated
stale_after: 2027-02-20          # OKF: declared expiry; optional
generated: { by: coscience-wiki/claude-sonnet-5, at: 2026-08-20T11:04:00Z }
verified:                        # OKF trust; written only by the platform on human action
  - { by: human:oleg, at: 2026-08-21T09:00:00Z }
sources:                         # OKF provenance family
  - { id: c14,  resource: /sources/result-p3-c14-beat-record-1572-gpu-result.md,
      title: "Sprint p3-c14 result", last_modified: 2026-08-14 }
  - { id: fig2, resource: /sources/artifact-takeoff-vs-hydrolysis-rate-v2.md,
      title: "Takeoff vs hydrolysis rate (v2)" }
relations:                       # co-science extension; frozen vocabulary (§7)
  - { type: requires,    target: /concepts/hydrolysis-rate.md,   confidence: high, source: c14 }
  - { type: contradicts, target: /concepts/supply-demand-gap.md, confidence: med,  source: fig2 }
aliases: [takeoff threshold]     # merge-first depends on this being maintained
---

# Definition

Takeoff occurs when per-base extension exceeds the
[hydrolysis rate](/concepts/hydrolysis-rate.md) at the operating length.[^c14]

# Evidence
# Contradictions
# Open questions
# Human notes

[^c14]: Sprint p3-c14 result
```

### 6.1 Why the OKF families replace what we would have invented

- **Trust.** An earlier draft of this design had `status: draft|reviewed|verified`
  — one field conflating two orthogonal axes. OKF separates them correctly:
  `status` is *lifecycle* (draft / stable / deprecated), while trust is *derived*
  from `verified`. No `verified` key → **unverified**; verified only by non-human
  actors → **machine-confirmed**; verified by a `human:` actor → **human-reviewed**.
  The UI's "Mark verified" button appends `{by: human:<user>, at: now}`; the tier
  falls out. Actor strings follow the OKF convention: `<producer>/<version>` for
  agents (`coscience-wiki/claude-sonnet-5`), `human:<id>` for people,
  `process:<id>` for automation.
- **Per-claim provenance.** `sources[].id` plus markdown footnotes (`[^c14]`)
  gives claim-level attribution as a real markdown mechanism, which is what the
  reference implementation's deep mode wanted from its
  `> Source: <title>, § <section>` convention. Renders everywhere; parses trivially.
- **Staleness.** `stale_after` is a per-page declared expiry. A kinetics parameter
  can be stale in three months; a definition never is. Lint checks the declared
  date first and falls back to an age heuristic only when it is absent (§9).

### 6.2 The protected section

`# Human notes` is **protected**: agents must never rewrite or remove it. This is
how "read + curate" survives re-ingest — a human correction is not overwritten
the next time the concept is touched. Enforcement is by lint
(`human-notes/removed`, checked against the file's previous git revision) rather
than by trusting the prompt, and the section is written through a dedicated
endpoint (§10) rather than by free-form editing.

### 6.3 Types

`type` is the only OKF-required field and types are not centrally registered.
We use `Concept`, `Entity`, `Synthesis`, `Source`, `Question`, matching the
directory the page lives in. Consumers must tolerate others.

- **Concept** — a reusable abstraction: a mechanism, method, phenomenon, regime,
  parameter, framework.
- **Entity** — a specific named thing: a person, tool, model, dataset, organism,
  gene, instrument, paper.
- **Synthesis** — a cross-source insight bundle that is valuable but is not a
  durable standalone node.
- **Question** — an open question with its context; mirrored in `QUESTIONS.md`.

---

## 7. The relation vocabulary

Frozen, in the manner of `graph.py`'s `EDGE_SPEC`. A relation type outside this
set is a lint error, not a silently accepted string — an open vocabulary
degenerates into thirty synonyms for "relates to" within a month.

| Type | Meaning | Legal (src → dst) |
|---|---|---|
| `is_a` | subtype / instance of | Concept→Concept, Entity→Concept |
| `part_of` | component of a larger whole | any→any |
| `requires` | dst must hold for src to hold | any→any |
| `enables` | src makes dst possible | any→any |
| `implements` | src is a concrete realization of dst | Entity→Concept |
| `exemplifies` | src is an instance illustrating dst | any→Concept |
| `measures` | src quantifies dst | any→Concept |
| `causally_precedes` | src causes or precedes dst | Concept→Concept |
| `contradicts` | src and dst cannot both hold | any→any |
| `refines` | src sharpens or corrects dst | any→any |
| `replaces` | src supersedes dst | any→any |
| `extends` | src builds on dst without replacing it | any→any |

Each relation carries `confidence: low | med | high` and `source: <sources[].id>`
naming which source supports it. **A relation with no `source` is a lint error**
— it is exactly the kind of unattributed assertion the wiki exists to prevent.

`contradicts` is symmetric in meaning but stored on one side only (the page that
asserted it). The graph builder materializes the reverse direction for display.
`replaces` and `causally_precedes` are directional and must stay acyclic; lint
reports cycles but does not block writes.

---

## 8. The ingest beat

### 8.1 Where it runs

Inside the **dispatch** loop, extending the per-program pass that already exists
in `Dispatcher.run_one_cycle` for the stale-chat-lock reaper.

The reasoning: dispatch is the platform's *supervision* loop — it already
launches, polls and collects detached Claude agents, already knows about pause
and usage gating, and already iterates programs once per cycle. The PM loop is
the *reasoning* loop and its cadence is tuned for planning. And a separate
process would change the deployment story in `deploy.sh` for no benefit.
**No new process; `deploy.sh` is unchanged.**

If wiki work ever starves sprint supervision, extracting it into its own loop is
a contained change — `wiki.beat()` is already a standalone entry point with a
CLI (`coscience wiki --once`).

### 8.2 Budget priority

Wiki maintenance is the **least urgent** consumer of the Claude budget. It must
never starve planning or sprint execution. A new threshold slots below the
existing ones:

```
WORKER_THRESHOLD        = 90.0   # sprint agents          (existing)
AUTONOMOUS_THRESHOLD    = 80.0   # PM loop beats          (existing)
WIKI_THRESHOLD          = 70.0   # wiki runs              (new)
WEEKLY_WORKER_THRESHOLD = 99.0   # weekly window          (existing, shared)
```

The gate is `claude_usage_ok(WIKI_THRESHOLD, weekly_threshold=WEEKLY_WORKER_THRESHOLD,
fail_open=False, repo_root=...)` — `fail_open=False` because an unmetered
autonomous loop is exactly what burns a window unattended, and `repo_root` so the
global pause is honoured first.

### 8.3 Run state

`.wiki/state.json`, guarded by a repo-level `flock` on `.coscience/wiki.lock`
(the same pattern as `artifacts._lock_guard`) because both the dispatcher and the
HTTP process can mutate it:

```json
{
  "ingested": {
    "result:p3-c14-…": { "hash": "sha256:…", "at": 1755680000.0, "run": "r0007" },
    "artifact:takeoff-vs-hydrolysis-rate@v2": { "hash": "sha256:…", "at": …, "run": "r0007" }
  },
  "ingests_since_lint": 3,
  "run": { "id": "r0008", "kind": "ingest", "batch": ["result:p3-c15-…"],
           "token": "48213:1755680001", "started_at": 1755680001.0,
           "model": "claude-sonnet-5" },
  "last_run": { "id": "r0007", "kind": "ingest", "status": "ok", "at": …,
                "pages_created": 6, "pages_updated": 3 },
  "failures": 0,
  "quarantined": []
}
```

`run` is `null` when nothing is in flight. **At most one run per program at a
time** — this is the concurrency model, and it is what makes merge-first safe.

### 8.4 The state machine

`wiki.beat(substrate, program, now, usage_gate, ...) -> str` returns a short line
for the dispatch beat summary. Called once per active program per cycle.

```
if program.status != ACTIVE or not program.wiki_enabled:   return ""

if state.run is not None:                       # a run is in flight
    if executor.is_running(state.run.token):    return "wiki: running"
    if not (run_dir/"agent.exit").exists():
        # Process gone, no exit code: the shell was killed between the two halves of
        # the launch command. Allow one grace beat for a slow filesystem, then give up
        # — without the deadline this branch returns "collecting" forever and the
        # program's wiki wedges silently.
        if now - state.run.started_at < COLLECT_GRACE:  return "wiki: collecting"
        status = "failed"
    else:
        status = collect(run_dir)               # 'ok' | 'failed'
    if status == "ok":
        verify no writes escaped the bundle (§13.1)
        mark every object in state.run.batch as ingested with its hash
        if kind == "ingest": ingests_since_lint += 1
        else:                ingests_since_lint = 0     # a lint run that finished
        failures = 0
    else:
        failures += 1
        if failures >= WIKI_MAX_FAILURES (3):
            failures = 0                    # unconditional: a lint run's batch is
                                             # always [], so gating the reset on a
                                             # non-empty batch lets lint failures push
                                             # this past threshold and never come back
            if state.run.batch: quarantine state.run.batch
    state.run = None; substrate.commit(...)
    return f"wiki: {kind} {status}"

if is_paused(repo) or not usage_gate():         return ""

if ingests_since_lint >= LINT_EVERY and bundle is non-empty:
    launch(kind="lint")     # the counter resets when the run collects ok, not here:
                            # a lint run that fails must still be owed
elif pending (excluding quarantined):
    launch(kind="ingest", batch=pending[:WIKI_BATCH])
else:
    return ""
```

Constants, all env-overridable:
`WIKI_BATCH = 4` (`COSCIENCE_WIKI_BATCH`), `LINT_EVERY = 5`
(`COSCIENCE_WIKI_LINT_EVERY`), `WIKI_MAX_FAILURES = 3`,
`COLLECT_GRACE = 60s`.

**Quarantine** is the key robustness property. A single object the agent cannot
process — a malformed result, an artifact version full of binary — must not wedge
the wiki forever. After three failures its batch is set aside, the beat moves on
to the next pending objects, and the quarantined ids are surfaced in the UI with
a "retry" action. Without this, one poison object silently stops all knowledge
accumulation for that program, and the symptom (nothing happening) is invisible.

### 8.5 Launching a run

`wiki_agent.launch()` renders the instructions into
`.wiki/runs/<rid>/instructions.md` and launches detached via
`executor.launch_detached`, **with cwd set to the bundle directory**:

```
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 claude -p "<prompt>" \
  --model <program.wiki_model> --disallowedTools Monitor \
  --dangerously-skip-permissions --output-format stream-json --verbose \
  > agent.out 2>&1; echo $? > agent.exit
```

`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` and `--disallowedTools Monitor` are
carried over from `ClaudeAgent._invocation` for the same reason they exist there:
a wiki run must not spawn work that outlives its turn. There is no
detached-job protocol here — a wiki run that cannot finish in one turn is a batch
that is too large, and the fix is a smaller `WIKI_BATCH`.

cwd is the bundle so relative paths in the agent's work are bundle-relative and
its `CLAUDE.md` loads automatically. Objects to read are given as **absolute
paths** in the prompt.

### 8.6 Completion signal

The sprint worker requires `finished.json` because a sprint's completion is a
judgement call. A wiki run's is not: it either wrote pages and exited 0 or it did
not. So:

- exit 0 → **ok**. The agent should also write `report.json`
  (`{pages_created: [...], pages_updated: [...], objects: [...], notes: "..."}`)
  for the beat summary and the UI; if it is missing, the run is still ok with
  unknown counts, and lint will find anything wrong.
- exit non-zero → **failed**, counted toward quarantine.
- no `agent.exit` and the process is gone → treated as failed (the run was killed).

No resume/nudge machinery in this design. A failed batch is simply retried on a
later beat.

### 8.7 Model

A new `Program.wiki_model` field, defaulting to `DEFAULT_MODEL`
(`claude-sonnet-5`), settable in `ProgramSettingsModal` alongside `pm_model`.
Bulk extraction is the right job for a cheaper model, and it is the least urgent
consumer of the budget. A new `Program.wiki_enabled: bool = True` lets a program
opt out entirely.

### 8.8 What the ingest prompt says

`wiki_prompts.render_ingest()` is pure and contract-tested. It carries, in order:

1. Role and the bundle path; the run is autonomous and unattended.
2. OKF v0.2 rules and the §6 frontmatter template verbatim.
3. The relation vocabulary (§7) and the containment invariant.
4. The reference implementation's normative rules (§2.2): merge-first,
   over-extract, source-title-is-never-a-concept, preserve layered insight.
5. The four-pass protocol: structure map → claim-level extraction → relationship
   triples → cross-wiki contradiction scan, each written out before the next.
6. The batch: for each object, its id, title, **absolute path(s) to read**, its
   precomputed `origin_hash`, and the platform-assigned source-page slug.
7. The protected-section rule (§6.2).
8. Housekeeping: update `index.md`, append to `log.md`, append to `QUESTIONS.md`,
   write `report.json`.
9. Prohibitions: do not write outside the bundle; do not background anything; do
   not compute hashes; do not delete pages.

`wiki_prompts.render_lint()` is the same shape over the machine lint report.

---

## 9. Lint

`wiki_lint.py` is pure: it takes parsed pages and returns findings. It runs in
two ways — as a plain script/CLI (cheap, deterministic, used by tests and by the
API for a live health badge) and as the basis of an agent **lint run** that fixes
what a machine cannot.

Ported from `docs/_tmp_wiki/llm-wiki-skills/skills/llm-wiki-lint/lint.py` and
extended for OKF and for approach C.

| Rule | Severity | Auto-fixable | Check |
|---|---|---|---|
| `okf/bad-yaml` | error | no | frontmatter does not parse |
| `okf/missing-type` | error | no | no non-empty `type` — breaks OKF conformance |
| `okf/index-frontmatter` | warn | yes | frontmatter on a non-root `index.md` |
| `link/wikilink` | warn | **yes** | `[[slug]]` → `[slug](/<dir>/<slug>.md)` |
| `link/broken` | warn | no | link target missing (OKF says tolerate: report, never fail) |
| `rel/unknown-type` | error | no | relation type outside §7 |
| `rel/no-link` | error | **yes** | **the approach-C invariant**: typed relation whose target is not linked in the body |
| `rel/no-source` | error | no | relation with no `source` key |
| `rel/dangling` | warn | no | relation target page does not exist |
| `rel/cycle` | info | no | cycle in `replaces` / `causally_precedes` |
| `page/stub` | warn | no | body under 200 characters |
| `page/stale` | warn | no | `stale_after` passed, else `generated.at` older than 180 days |
| `page/orphan` | info | no | no inbound links and absent from `index.md` |
| `page/duplicate-slug` | error | no | same slug in two directories |
| `page/near-duplicate` | warn | no | title/alias overlap above threshold |
| `src/hash-drift` | error | no | origin object's current hash ≠ `origin_hash` |
| `src/missing` | error | no | origin object no longer exists |
| `src/is-concept` | error | no | a concept page whose title closely matches a source page title — the article was turned into a concept |
| `human-notes/removed` | error | no | a page that had `# Human notes` in its previous git revision no longer does |
| `trust/unverified-stable` | info | no | `status: stable` with no `verified` entry |

Mechanical auto-fixes are applied **by the script before the agent runs** —
deterministic and free. The agent's lint run then handles judgement calls:
merging near-duplicates (proposing, never deleting without a human), resolving
contradictions, refreshing drifted source pages, filling missing sources. It
writes `.wiki/lint/<date>.md` and appends to `log.md`.

`link/broken` is a warning, never an error, because OKF requires consumers to
tolerate broken links and because in a living wiki a broken link often marks
knowledge not yet written.

---

## 10. Graph

`wiki_graph.py` is pure — parsed pages in, `{nodes, edges}` out. No IO.

- **Nodes**: pages of type `Concept`, `Entity`, `Synthesis`, excluding any page
  with `graph_excluded: true`. Source pages are therefore never nodes (§2.2).
- **Edges**:
  - *typed*, from `relations` — carrying `type`, `confidence` and the `source` id;
  - *untyped*, from body markdown links between node pages, minus any pair
    already covered by a typed relation.
- **Node metrics**: in-degree, out-degree, `orphan` (degree 0), `cluster`
  (connected-component id — deterministic and sufficient; community detection is
  a later refinement), plus `status` and derived `trust` for colouring.
- **Cache**: `.wiki/graph.json`, keyed by a digest over every page's
  `(path, mtime, size)`. A mismatch rebuilds. Rebuild is O(pages) and cheap into
  the thousands.

Frontend layout: `dagre` (already a dependency) is hierarchical and wrong for a
knowledge graph, which has no root and is not a DAG. Add **`d3-force`** — one
small, well-tested dependency, sitting behind the existing `graphLayout.ts` seam
so `LineageGraph` is untouched. Rendering stays `@xyflow/react`.

---

## 11. API and UI

### 11.1 Endpoints

All under the gated `api` router in `http_api.py`, delegating to `Service`.

```
GET    /api/programs/{pid}/wiki                       summary
GET    /api/programs/{pid}/wiki/pages                 list
GET    /api/programs/{pid}/wiki/pages/{slug}          page + backlinks + relations + provenance
GET    /api/programs/{pid}/wiki/search?q=             keyword search
GET    /api/programs/{pid}/wiki/log                   log.md
GET    /api/programs/{pid}/wiki/lint                  latest report + live findings
POST   /api/programs/{pid}/wiki/run                   force a run  {kind: ingest|lint}
POST   /api/programs/{pid}/wiki/unquarantine          clear quarantined objects
POST   /api/programs/{pid}/wiki/pages/{slug}/verify   append {by: human:<user>, at: now}
POST   /api/programs/{pid}/wiki/status/{slug}         set draft|stable|deprecated
POST   /api/programs/{pid}/wiki/notes/{slug}          write the protected # Human notes section
DELETE /api/programs/{pid}/wiki/pages/{slug}          delete; drops inbound relations
GET    /api/programs/{pid}/wiki/graph                 nodes + edges            (phase 4)
```

`{slug}` is a path segment and **must be containment-checked against the bundle
root after symlink resolution**, exactly as `artifacts.resolve_sources` and
`fs_browse` do. A slug is otherwise a traversal primitive.

`summary` returns: page counts by type, trust-tier breakdown, pending-object
count, quarantined ids, `last_run`, `run` (if in flight), `ingests_since_lint`,
lint error/warning counts, and `index.md` rendered.

### 11.2 Browse UI — `/programs/:id/wiki`

Three panes, in the Obsidian idiom, reusing what exists:

- **Header** — counts by type, trust breakdown, pending count, last run and its
  outcome, lint badge, "Ingest now" / "Lint now", quarantine warning with retry.
- **Left** — search box; tree grouped by type (Concepts / Entities / Syntheses /
  Sources / Questions), each row carrying a trust dot and a stale marker.
- **Centre** — the page rendered with `Md.tsx`. Internal markdown links are
  intercepted and routed client-side rather than navigating away. Footnote
  references render as citations. `sources` become **provenance chips** linking
  to `/results/:id`, `/sprints/:id` and `/programs/:id/artifacts/:aid` — this is
  the payoff for building the wiki inside the platform rather than beside it.
- **Right** — outline (`PageToc.tsx`), backlinks, typed relations grouped by
  type, and a trust panel: "Mark verified", status select, and the `# Human notes`
  editor.

`ProgramDetail` gains an "open wiki →" link beside ideas and artifacts, with the
pending-object count as a badge.

### 11.3 Graph UI — `/programs/:id/wiki/graph` (phase 4)

Force-directed. Filters by node type, relation type and trust tier; a
**typed-only toggle** (the visible half of approach C); orphans highlighted;
click a node to open its page. Node size by degree, colour by type, edge style
by relation family.

---

## 12. Modules, seams and testing

```
src/coscience/
  wiki_store.py     bundle IO: parse/serialize pages, list, slug↔path, state.json + its flock
  wiki_okf.py       OKF frontmatter model + conformance checks              (pure)
  wiki_graph.py     graph construction                                     (pure)
  wiki_lint.py      lint rules + mechanical fixes                          (pure)
  wiki_prompts.py   instruction rendering                                  (pure)
  wiki_agent.py     launch / poll / collect a detached run   ← the ONLY side-effecting seam
  wiki.py           beat(): the §8.4 state machine; called by Dispatcher
  agent_stream.py   NEW, shared: parse a stream-json capture               (pure)
```

**`agent_stream.py` is a small extraction, not new scope.** `ClaudeAgent._unwrap_envelope`
and `chat_agent.collect_turn` already contain two near-identical copies of the
stream-json scan; this design would add a third. A pure
`parse_stream(raw) -> {result_text, session_id, usage, cost, turns, duration_ms}`
is extracted and all three callers keep their own status logic (which genuinely
differs: `interrupted` handling, the cost sidecar, session-id capture). Behaviour-
preserving, covered by the existing tests for those two paths.

`wiki_agent` is injectable in the same way `pm_claude.ClaudeCodeReasoner` sits
behind the reasoner seam, so **the unit suite never calls a live LLM.** The test
double writes canned pages into a tmp bundle and exits 0.

Frontend:

```
frontend/src/views/WikiView.tsx          browse            (phase 2)
frontend/src/views/WikiGraphView.tsx     graph             (phase 4)
frontend/src/components/wikiPage.ts      pure: link rewriting, backlinks   + test
frontend/src/components/wikiGraph.ts     pure: graph → xyflow nodes/edges  + test
```

**Testing strategy**, matching the existing suite's shape:

- Pure modules unit-tested directly; one test per lint rule against fixture
  bundles.
- `wiki.beat()` tested against a tmp substrate with a fake agent: pending
  detection, batching, ordering, hash-drift re-ingest, lint cadence, failure
  counting, quarantine, and the one-run-at-a-time invariant.
- `wiki_prompts` contract-tested (the batch block contains every object, absolute
  paths, precomputed hashes).
- Frontend: `vitest` on `wikiPage.ts` and `wikiGraph.ts`, matching
  `graphFlow.test.ts`.

---

## 13. Risks and failure modes

Stated plainly, including the ones not fully solved.

**13.1 An agent writes outside the bundle.** Runs use
`--dangerously-skip-permissions`; cwd is a convention, not a sandbox. *Mitigation:*
the prompt is explicit, and after each run the beat checks `git status` on the
substrate and **refuses to mark the batch ingested** if anything changed outside
`programs/<pid>/wiki/` and `programs/<pid>/.wiki/`, surfacing it in the UI.
Detection, not prevention — the run is not auto-reverted, because reverting a
human's concurrent edit would be worse than reporting. *Residual risk accepted*,
consistent with how the rest of the platform runs its agents.

**13.2 Budget starvation.** Mitigated by `WIKI_THRESHOLD = 70` (§8.2) and
one-run-per-program. If wiki runs still crowd out sprints, the lever is the
threshold, then `WIKI_BATCH`, then `wiki_enabled: false`.

**13.3 Hallucinated or low-quality concepts.** The real risk of the whole
pattern. Mitigated but **not eliminated** by: mandatory `sources` and per-relation
`source`; lint's `rel/no-source` and `src/is-concept`; trust tiers making
unverified content visibly unverified; and human verify. A wiki page is evidence
of what an agent concluded, not proof that it is true, and the UI should not
imply otherwise.

**13.4 Duplicate and near-duplicate pages.** The classic llm-wiki failure at
scale. Mitigated by merge-first, `aliases`, `page/near-duplicate`, and
one-run-at-a-time (concurrent ingests are what produce duplicates most reliably).
A merge action is phase 5.

**13.5 Concurrency between dispatcher and HTTP.** Both can mutate `state.json`.
Mitigated by the `flock` on `.coscience/wiki.lock` around every read-modify-write.

**13.6 Commit noise.** The wiki adds substrate commits. This is intended — it is
what gives page history — but it makes `git log` busier. Wiki commits are prefixed
`wiki <pid>:` so they filter cleanly.

**13.7 Large bundles.** A thousand-page program makes full listing and graph
construction non-trivial. Mitigated by the mtime-keyed `graph.json` cache and a
small in-memory page cache. Beyond a few thousand pages this design would need
an index; that is out of scope and noted in §14.

---

## 14. Deferred, by design

Not built now. Listed because the schema deliberately leaves room for each, so
none is a retrofit.

- **External literature intake.** Papers, PDFs, URLs. Drops into
  `programs/<pid>/wiki-raw/` with conversion (`marker` → `pymupdf4llm` →
  `pdftotext`, the reference implementation's ladder) and a UI drop zone. The
  schema already accommodates it: such a source page is simply
  `origin: file:wiki-raw/foo.pdf` with the same `resource` + `origin_hash`
  shape. **This is the first item on the backlog** and the one most likely to be
  wanted next.
- **Attested Computation.** OKF v0.2's `type: Attested Computation` carries a
  definition *and* the sanctioned code that computes it, with an `executor`
  returning a receipt and an `attester` verifying the receipt matches. For this
  platform that means a concept page can pin a computed value to the artifact
  version and script that produced it, making **"re-verify this claim" a sprint
  the platform can run**. That is reproducibility as a first-class citizen, and
  it is the single most valuable thing this subsystem could grow. §5 and §6 are
  shaped to allow it without migration.
- **Cross-program wiki.** A platform-level bundle linking program bundles.
- **Semantic search / embeddings** over the bundle.
- **Page merge UI** for the near-duplicate findings.
- **Making `results/` itself OKF-conformant**, per `initial_specs.md:26`.

---

## 15. Phasing

The first shippable milestone is **ingest + browse** (phases 1–2). Each phase
gets its own implementation plan.

| Phase | Contents | Done when |
|---|---|---|
| **1 — Store & ingest** | `wiki_store`, `wiki_okf`, `wiki_prompts`, `wiki_agent`, `wiki.beat` wired into `Dispatcher`, `agent_stream` extraction, `Program.wiki_model` / `wiki_enabled`, bundle `CLAUDE.md` template, `wiki_lint` as a CLI-only script, `coscience wiki --once` | a real program's results and artifacts produce a valid, linted bundle from the CLI |
| **2 — Browse** | Service methods, endpoints, `WikiView`, curation actions, provenance chips, `ProgramDetail` link | **first milestone**: you can read the wiki in the dashboard and mark pages verified |
| **3 — Lint runs** | agent lint mode, lint cadence live, report UI, quarantine retry | the wiki self-maintains |
| **4 — Graph** | `wiki_graph`, endpoint, `WikiGraphView`, `d3-force`, provenance backlinks on SprintDetail / ArtifactDetail | you can explore the concept graph and click through to evidence |
| **5 — Ask & research** | wiki-scoped chat thread, research/gap-analysis run mode, `QUESTIONS.md` flow, MCP `wiki_search` / `wiki_read` / `wiki_neighbors`, "consult the wiki first" in the sprint worker prompt | agents and humans both query the wiki instead of the raw pile |

Phase 5 is where the user-facing goal — *"ask questions about the wiki, have the
LLM do research and form hypotheses from it, and give agents access to it"* —
lands. Phases 1–4 exist to make that phase have something worth querying.
