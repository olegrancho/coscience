# Research Lineage Graph — Design

**Date:** 2026-07-16
**Status:** approved for planning
**Supersedes (partially):** the never-built "provenance as first-class frontmatter"
concept in `2026-06-23-co-science-platform-design.md:99-101,339-342`, which
scoped provenance to result→result only. This design generalizes it to the
idea/experiment lifecycle.

## 1. Goal

Represent a program's **ideas** and **experiments** as nodes in a typed-edge
graph so the intellectual lineage of the research is persistent, navigable, and
usable by the PM agent for planning. Today ideas and sprints are flat lists with
no durable relationships: promotion destroys the idea→sprint link (`pm_agent.py`
pops the idea and the sprint stores nothing), demotion mints an unrelated new
idea, and there is no edge/graph concept anywhere.

## 2. Core model

### 2.1 One node, three lifecycle stages

A node is a single research thing progressing through stages:

```
idea → experiment → result
```

- **idea** — a candidate direction (existing `Idea`, one program's `ideas.md`).
- **experiment** — an executable unit of work (existing `Sprint`).
- **result** — **not a separate node type.** A result is an experiment with
  status `done`. `Sprint.status == done` *is* the result stage.

Consecutive stages never coexist. Promotion consumes the idea into the
experiment (already decided); reaching `done` turns the experiment into its
result in place. This mirrors the promote-consume rule and keeps the node count
minimal — no parallel `Result` node, no new file kind. (The thin existing
`Result` dataclass is unaffected by this design; it is not a graph node.)

**Multiplicity simplification:** result == finished experiment (1:1). A real
experiment can yield several findings; we deliberately collapse to one. Revisit
only if a concrete need appears (YAGNI).

### 2.2 Edges

Edges are typed, directed, and stored in a single **canonical direction**; the
inverse is rendered in the UI and never stored. Every edge is one of a
**frozen enum** (adding a type is a code change + review, like `SprintStatus`).

Edges split into two families:

- **Lineage edges** — structural genealogy. Must form a **DAG**: an `add` that
  would close a cycle is rejected at the apply seam.
- **Evidential edges** — what a *result* says about another result. Only
  assertable when **both endpoints are done experiments** (result stage). May
  form cycles (two results can mutually `contradict`); segregated from the DAG
  invariant.

Vocabulary ships in two tiers. Both tiers are defined in the enum from day one;
**Core** is built and exposed first, **Extended** is enabled once Core works.

**Core (build first):**

| edge | family | pair | meaning | source |
|---|---|---|---|---|
| `inspired_by` | lineage | idea→idea (or cross-stage) | B was provoked by A | asserted |
| `builds_on` | lineage | exp→exp | B uses A's method/output | asserted |
| `supersedes` | lineage | exp→exp | B obsoletes A (better design/scope) | asserted or system |
| `confirms` | evidential | exp→exp (both done) | B's result supports A's | asserted |
| `refutes` | evidential | exp→exp (both done) | B's result overturns A's | asserted |

**Extended (enable after Core):**

| edge | family | pair | meaning |
|---|---|---|---|
| `refines` | lineage | idea→idea | B is a narrower cut of A |
| `follows` | lineage | exp→exp (or idea) | B should run after A (sequencing; no data reuse implied) |
| `replicates` | lineage | exp→exp | B re-runs A to test reproducibility |
| `duplicate_of` | lineage | same-stage | dedup pointer (PM merge) |
| `contradicts` | evidential | exp→exp (both done) | symmetric unresolved conflict (neither designated wrong) |

**Dropped (do not implement):** `related` (untyped dumping ground, zero planning
value, top edge-spam magnet), `motivated_by` (synonym of `inspired_by`),
`refuted_by` (inverse of `refutes` — render, don't store), `contradicts` at
experiment level unless the unresolved-conflict distinction is wanted (kept in
Extended only).

### 2.3 Edge attributes

Every edge is a dict, not a bare id:

```
{ id, type, src, dst, source, by, at, rationale, confidence, evidence }
```

- `source` ∈ `{system, pm, human}` — provenance of the edge itself.
- `by` — author id (username, `"pm"`, or `"system"`); `at` — timestamp.
- `rationale` — one-line justification. **Required for every asserted edge**
  (empty allowed only for `source == system`). Turns a fuzzy type into an
  auditable claim.
- `confidence` ∈ `{low, med, high}` — **required on evidential edges**, omitted
  on lineage edges.
- `evidence` — optional free-text/id citing the deciding result; expected on
  evidential edges, treated as low-confidence if absent.

**Pinning:** edges with `source ∈ {human, system}` are pinned — the PM may not
delete them (mirrors the pinned-idea rule). The PM may only add, and may only
delete edges it authored (`source == pm`).

## 3. Storage

**Hybrid**, following the existing git-markdown + rebuildable-index pattern:

1. **Authoritative: outbound edge lists in each node's frontmatter.**
   - Ideas: a new `edges: [...]` key inside each idea dict in `ideas.md`
     (per-idea outbound list). `load_ideas`/`save_ideas` gain the field;
     back-compat because unknown/missing keys default to empty.
   - Experiments: a new `edges: [...]` frontmatter key on `sprint.md`.
     `load_sprint` uses `fm.get(...)`, so an added key is back-compatible.
   - Each edge is stored on the **source** node. Storing outbound-on-node
     localizes writes (a git diff shows who asserted what) and avoids a single
     per-program edges file that every promotion and human assertion would
     contend on (maximal merge conflicts). Serialize edge lists sorted and
     one-edge-per-line so concurrent additions merge cleanly.

2. **Derived: a rebuildable reverse index.** `.coscience/graph.json` (or
   in-memory, rebuilt on load) mapping `node_id → [incoming edges]`. Never
   hand-edited; disposable like the search index. Answers "who points AT this
   node?" in O(1) — required for promote/demote rewiring and delete integrity.

## 4. Lifecycle transitions rewire edges (no lasting transition edge)

Transitions are **node-identity migrations**, not edges. None of them persists a
`promoted`/`demoted`/`finished` edge.

**Two-part rule (applies to both promote and demote).** The transition itself
does a **deterministic** edge rewire — no LLM in the write path, so integrity is
guaranteed and testable the instant the transition commits (this respects the
reasoner-seam rule: writes stay out of the reasoner). The PM then **curates** the
result on its next cycle: it sees the updated graph (§5.2) and issues normal
`edge_ops` to re-assert, retype, or drop edges the mechanical rewire got wrong.
So "ask the PM to update/create nodes on promote/demote" happens through the
standard `edge_ops` seam the following cycle — not as a special blocking call
inside the transition.

### 4.1 Promote (idea A → experiment SA)

1. Create sprint `SA` (existing path).
2. **Repoint every edge incident on A onto SA — both directions.**
   - Outbound edges on A move to SA's edge list.
   - Inbound edges (found via the reverse index: any node with an edge whose
     `dst == A`) are rewritten so `dst = SA`.
   - Edge `type` is preserved. A lineage edge that becomes cross-stage after
     rewire (e.g. `B inspired_by A` → `B inspired_by SA`) is **allowed** —
     cross-stage lineage edges are legal.
3. **Hard-delete A.** No tombstone. The idea space is intentionally
   noisy/fuzzy; a graveyard of consumed ideas is unwanted, and repointing both
   directions means nothing dangles. Git history preserves the "was once idea A"
   fact; no in-app trace is kept (the `promoted` marker was explicitly dropped).

### 4.2 Demote (experiment SA → idea)

Deterministic rewire now, PM curation next cycle (per the two-part rule above):

1. Mint the demoted idea (existing `demote_sprint` path, auto-pinned +
   `demoted=True`).
2. Repoint SA's incident edges (both directions, via the reverse index) onto the
   new idea where still valid; **drop evidential edges** (an idea has no result
   to confirm/refute).
3. Hard-delete SA's node identity as today (sprint set `CANCELED`); its edges do
   not linger on a canceled node.
4. Next cycle the PM sees the before/after pair in its windowed graph and
   re-asserts appropriate lineage via `edge_ops`.

### 4.3 Finish (experiment → result)

No migration — the same node crosses into the result stage when
`status == done`. This only *unlocks* evidential edges targeting it; nothing is
rewired.

## 5. PM as producer and consumer

### 5.1 Emitting edges — `PMCycleOutput.edge_ops`

Add one field to `PMCycleOutput` (`pm_reasoner.py`):

```python
edge_ops: list[dict] = field(default_factory=list)
# [{op: "add"|"delete", type, src, dst, rationale, confidence?, evidence?}]
```

Applied deterministically in `pm_beat`/`_run_pm_cycle`, exactly like
`sprint_edits`/`delete_idea_ids`:

- Validate `type` ∈ enum, `src`/`dst` resolve to **live** nodes, direction/pair
  legal for the type, evidential endpoints both `done`.
- **Silently drop** any op that fails validation (hallucinated endpoints,
  illegal type) — same forgiving pattern as `coerce_resources`. Kills bad edges
  at the seam, not in the model.
- `add` that would close a lineage cycle → dropped.
- `delete` of a pinned (human/system) edge → ignored.
- Dedup; require non-empty `rationale` on every `add` (drop `add`s without one).
- Cap edges added per cycle (constant, e.g. `MAX_EDGE_OPS`) to bound spam.

The staging file (`.pm/cycle-staging.json`) round-trips `edge_ops` so a killed
cycle resumes safely (same as `directive`/`idea_order` today).

### 5.2 Presenting the graph to the PM — windowed

Do **not** dump the whole graph (context budget). In `gather_context` /
`render_prompt`:

- For each node already in the prompt (idea pool + open + recently-completed
  sprints), render a compact adjacency line: `SA builds_on SB; refutes SC`.
- Collapse the distal subgraph to cluster summaries:
  `cluster X: 6 experiments, 4 confirmed, 1 dead-end`.
- Scales with the window already rendered, not with program size.

The PM emits **diffs** (`edge_ops`), never a full re-declaration, so it can't
churn the whole graph each cycle.

### 5.3 Planning payoff (why this earns its keep)

With persistent lineage the PM can, deterministically from the graph rather than
re-reading prose each cycle:

- **Avoid dead directions** — skip proposing against a node with an incoming
  `refutes` or that is `superseded`.
- **Spot dead-ends** — a `builds_on` chain whose leaves are all refuted is a
  branch to abandon.
- **Dedup proposals** — a proposal that would `builds_on`/`duplicate_of` an
  existing node is likely redundant with pending work.
- **Prune with lineage awareness** — an idea whose `inspired_by` ancestor was
  refuted is a cheap prune.
- **Prioritize the frontier** — order releases toward `confirms`-supported
  branches over contested ones.

## 6. Frontend

No graph library exists today (no d3/reactflow/cytoscape). The viz is a distinct
build phase after the data model + PM seam land:

- Program-scoped graph view: nodes colored by stage (idea / experiment /
  result) and by experiment outcome; edges styled by family (lineage solid,
  evidential dashed) and by `source` (system vs pm vs human visually distinct).
- Edge hover shows `rationale`, `by`, `confidence`.
- Humans assert/delete edges from this view (human edges are pinned).
- Library choice (reactflow vs cytoscape vs lightweight SVG) is a phase-time
  decision, deferred to the plan.

## 7. Failure modes + mitigations

| risk | mitigation |
|---|---|
| Edge-type sprawl | frozen enum; new type = code change + review. Ship Core, hold the line. |
| Edge spam (LLM links everything) | drop `related`; per-cycle cap; mandatory `rationale`; edge must change navigation to be worth asserting. |
| Hallucinated edges (bad endpoints) | validate + silently drop at the apply seam. |
| Dangling refs on delete | soft never needed — hard-delete only after repointing both directions via reverse index; a validator/heartbeat pass logs any orphan. |
| Subjective typing (`builds_on` vs `inspired_by`) | mandatory rationale makes it auditable; coarse, small vocabulary; the two families are structurally separated. |
| Over-asserting `confirms`/`refutes` | gated to done nodes; require `confidence`; expect `evidence` citation; render uncited as low-confidence; evidential edges votable/reviewable like sprints. |
| Cross-cycle drift (add then reverse) | edges persist; PM emits diffs against the shown graph, not full redeclare. |
| Git merge conflicts | outbound-on-node storage localizes writes; sorted one-edge-per-line lists; reverse index rebuilt, never merged. |
| Direction confusion | single canonical direction per type; inverse rendered only. |

## 8. Scope / phasing (for the plan)

1. **Data model + storage** — edge enum, edge dict shape, `edges` on Idea and
   Sprint, `load/save` round-trip, reverse-index build/rebuild. Pure backend +
   tests. No behavior change yet.
2. **Lifecycle rewiring** — promote repoints both directions + hard-deletes idea;
   demote drops evidential + surfaces pair; done unlocks evidential. Tests.
3. **PM seam** — `edge_ops` in `PMCycleOutput`, deterministic apply with full
   validation, staging round-trip, windowed graph in the prompt. Tests +
   `FakeReasoner`.
4. **HTTP + service** — endpoints to add/delete human edges (pinned), read the
   graph for a program.
5. **Frontend graph view** — library, render, hover, human assert/delete.
6. **Extended edge tier** — enable `refines`/`follows`/`replicates`/
   `duplicate_of`/`contradicts` once Core is proven.

Each phase is independently testable and lands working software. Phases 1-4 are
backend and can ship before any viz exists (the graph is usable by the PM before
it's visible to humans).

## 9. Out of scope

- Multiple findings per experiment (1:1 result collapse; revisit on need).
- Cross-program edges (edges are within one program).
- Bitemporal / edge versioning beyond git history + `at` timestamps.
- Auto-inferred edges from text similarity (all edges are asserted or from a
  lifecycle transition).
