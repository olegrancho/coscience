# Program wiki, phase 4 — the concept graph

**Date:** 2026-08-28
**Status:** design, approved in conversation; not yet planned or implemented.
**Parent spec:** `2026-08-20-program-wiki-design.md` — **binding**. This document
refines its §11.4, which is one paragraph, and settles questions its §10 and
§11.4 leave open. Where this document and the parent disagree, the parent wins
*except* on the three points listed in §8 below, which are deliberate
amendments and carry their reasoning.

Phase 4's done-condition, from the parent's §15 table: *you can explore the
concept graph and click through to evidence.*

---

## 1. What this phase builds

| Piece | Where |
|---|---|
| Graph builder, pure | `src/coscience/wiki_graph.py` (new) |
| Service method + endpoint | `Service.wiki_graph`, `GET /api/programs/{pid}/wiki/graph` |
| Force layout, behind the existing seam | `frontend/src/components/graphLayout.ts` |
| Full-page view | `frontend/src/views/WikiGraphView.tsx` (new) |
| Neighbourhood pane | inside `WikiView`'s existing `.wiki-side` aside |
| Provenance backlinks | `wiki_read`, surfaced on `SprintDetail` and `ArtifactDetail` |

Out of scope, and staying that way: community detection (the parent defers it
in its §10), server-side layout, graph editing. A node is read-only; curation stays in
the browse view.

## 2. The data

`wiki_graph.py` is pure — parsed pages in, `{nodes, edges}` out, no IO — so it
tests without a substrate, exactly as `wiki_lint.lint()` does.

**Nodes** are pages of type `Concept`, `Entity` or `Synthesis`, minus any page
with `graph_excluded: true`. Source pages are therefore never nodes (parent
§2.2), which lint rule `src/is-concept` already assumes; do not re-derive it.

```
node: { id, slug, title, type, status, trust,
        in_degree, out_degree, orphan, cluster }
edge: { id, src, dst, type, confidence, source, typed, materialized }
```

`trust` calls `wiki_read.trust_tier` rather than re-deriving the tiers. `type`
is `""` on an untyped edge. `cluster` is a connected-component id.

**Edges** are typed (from `relations`, carrying `type`/`confidence`/`source`)
and untyped (body markdown links between two node pages, minus any pair a typed
relation already covers).

**Materialized reverse edges.** Parent §7: `contradicts` is symmetric in meaning
but stored on one side only, and the graph builder materializes the reverse for
display. Such an edge carries `materialized: true` and is **excluded from
`in_degree`, `out_degree`, `orphan` and `cluster`**. Node size is degree, so
counting a materialized edge would make both ends of one disagreement look
better-connected than they are — the picture would state something false.

**Cache.** `.wiki/graph.json`, rebuilt when its key does not match. See §8.1 for
the key.

## 3. The endpoint

```
GET /api/programs/{pid}/wiki/graph   ->  { nodes, edges }
```

On the gated `api` router in `http_api.py`, delegating to `Service.wiki_graph`,
like every other wiki route. No new auth surface.

## 4. Layout

`graphLayout.ts` today exports `layout()`, backed by `dagre`. Phase 4 **adds**
two functions and changes neither the existing one nor its callers, so
`LineageGraph` cannot regress:

- `forceLayout(nodes, edges)` — `d3-force`, for the full-page view. `d3-force`
  is a new frontend dependency; `dagre` stays for lineage. The parent §10 is
  explicit that dagre is hierarchical and wrong for a knowledge graph, which
  has no root and is not a DAG.
- `radialLayout(centre, neighbours)` — trivial, deterministic, no dependency.
  Used by the neighbourhood pane (§6).

`WikiGraphView` is lazy-loaded (`lazy(() => import(...))`), following
`LineageCard`, so `d3-force` stays out of the main bundle.

**Determinism.** Checked empirically during the phase, not assumed:
`d3-force@3`'s own jiggle (`node_modules/d3-force/src/lcg.js`) is a fixed-seed
linear congruential generator, not `Math.random` — so it is already
deterministic on its own, and the earlier justification for seeding was wrong.
Initial positions are still seeded from node index, for two reasons that don't
depend on d3's internals: it avoids relying on d3's `isNaN`-triggered fallback
positioning for nodes that start coincident, and it makes our determinism
explicit and ours rather than inherited from a dependency's implementation
detail that could change in a future major version. The simulation still runs
a fixed tick count synchronously. Same graph in, same picture out. Manual drags
persist per program in `localStorage` through the existing `graphPositions.ts`.

**Route ordering.** `App.tsx` sends `/programs/:id/wiki/*` to `WikiView` as a
catch-all for page slugs, and `/wiki/graph` is registered before it, as
`/wiki/lint` already is. **Tested empirically during the phase: this ordering
is not load-bearing.** React Router v6 ranks sibling routes by path
specificity — a static segment outranks a trailing `*` splat — independent of
declaration order, so moving `/wiki/graph` after the catch-all changed
nothing. The original justification (declaration order decides the match) was
wrong for v6; the route's current position is fine and is kept for
readability, not correctness. Phase 3 shipped a route-ordering test that could
not detect mis-ordering — see §9, whose test now guards resolution rather than
order.

## 5. The full-page view — `/programs/:id/wiki/graph`

### 5.1 Encoding

Structure is the base. It reuses the browse view's visual language rather than
inventing a second one, so a marker means the same thing in both places.

| Channel | Carries |
|---|---|
| Hue | page type — Concept, Entity, Synthesis |
| Fill | trust — hollow (`unverified`), tinted (`machine-confirmed`), solid (`human-reviewed`), matching `.wiki-dot--*` |
| Size | degree, materialized edges excluded |
| Ring | `orphan` |
| Dimmed + struck | `status: deprecated` |

Edges default to thin grey with a direction arrow. Untyped body-link edges are
fainter and dotted, so approach C's two layers read apart without the toggle.

`status` is otherwise a filter, not an encoding. Adding a fifth visual channel
buys less than it costs in legibility.

### 5.2 The tension lens

A toggle, not a second view. It restyles edges only:

- `contradicts` — thick red, drawn **once** as a double-headed edge rather than
  as two arrows, since the pair is one disagreement;
- `replaces`, `refines` — amber, arrowed;
- everything else drops near-invisible, and nodes touching no tension edge dim.

**Layout does not change between lenses.** Same positions, same graph, paint
only. A toggle that rearranges the picture is one the reader stops trusting.

### 5.3 Filters

Node type, relation type, trust tier, and the typed-only toggle the parent
§11.4 names (the visible half of approach C). Two semantics are fixed
deliberately:

- **Filtering hides; it never re-layouts.** Positions are computed once over
  the whole graph. Otherwise every checkbox makes the map jump.
- **Filtering never manufactures orphans.** `orphan` is a whole-graph property
  computed server-side. A node stranded only because its edges were filtered
  away must not gain an orphan ring — that reports a data problem that does not
  exist.

A "showing N of M nodes" readout, so a filter cannot hide things silently.

### 5.4 Scale

Expected range is **50–200 nodes** (stated by the human, 2026-08-28); the view
draws everything by default and needs to stay readable at the top of that range.

Connected-component collapsing is **not** the lever. `cluster` is a connected
component, and a healthy wiki is mostly one giant component with a few strays —
collapsing would yield one blob and three specks. The levers that work:

1. labels hidden below a zoom threshold, as `LineageGraph`'s `DotNode` does;
2. the §5.3 filters;
3. **focus mode** — click a node, reduce to its N-hop neighbourhood. This is the
   same code as the neighbourhood pane (§6), reached as
   `/wiki/graph?focus=<slug>`.

Thousands-of-nodes handling — worker layout, virtualisation — is out of scope
and would be its own phase.

## 6. The neighbourhood pane

In `WikiView`'s existing `.wiki-side` aside. Centre is the page being read, one
hop by default, with a two-hop toggle. Clicking a neighbour navigates and
re-centres. "Open full graph ↗" goes to `/wiki/graph?focus=<slug>`.

It uses `radialLayout`, **not** `d3-force`: a one-hop neighbourhood is typically
under ten nodes, so a deterministic radial placement is enough, and the browse
view — the most-opened view in the wiki — takes on no new dependency and no lazy
chunk.

**Source pages.** They are excluded from the graph (§2), so a Source page has no
neighbourhood. Rather than an empty box, the pane says so and lists the pages
citing this source — the same data as §7's backlinks, one query serving both.

## 7. Provenance backlinks

Phase 2 built the forward direction: a wiki page shows the sources it cites.
Phase 4 adds the reverse — a result or artifact version shows which wiki pages
cite *it*, closing the loop the parent §15 asks for ("click through to
evidence", in both directions).

The chain already exists in the data, so this is a lookup, not new plumbing:

```
result:wt-r1 ──origin──▶ sources/result-wt-r1.md ──sources[].resource──▶ concepts/ivywrel-correlation.md
  (object)                   (Source page)                                   (what cites it)
```

Given an object id, find the Source page whose `origin` matches, then every page
whose `sources[].resource` points at it. This is pure and belongs in `wiki_read`
beside `provenance_ref` — it is not graph data, it only ships in the same phase.

The endpoint takes the object id as `{oid:path}`, not a bare `{oid}`. Checked
empirically during the phase: a plain-`@` id (an artifact oid's shape) routes
fine under a bare converter — `@` is not a path separator and does not trip
FastAPI's default matcher. The converter is needed because **a slash-containing
id** (an artifact version id can contain `/`) would otherwise be split across
path segments and 404. Reach for `:path` on that basis, not on `@`.

Surfaced as chips on `SprintDetail` (for its results) and `ArtifactDetail` (for
the version on screen).

**Artifact versions need care.** `wiki_store.program_objects` ingests an
artifact's **current version only** — deliberately, so a figure revised five
times leaves one page trail instead of five near-identical source pages. Any
non-current version therefore has zero backlinks, which is correct behaviour but
reads as broken when rendered as an empty row. It must say "not ingested — only
the current version is" rather than render nothing.

## 8. Deliberate deviations from the parent spec

Each needs a row in `docs/knowledge-charter.md` §7 when implemented, so it does
not later read as drift.

1. **Cache key is a content hash, not `(path, mtime, size)`.** The parent §10
   specifies `(path, mtime, size)`. `mtime` changes on every `git checkout`
   while content does not, and content can change while size does not — the
   second case serves a stale graph. At 200 pages a rebuild is milliseconds, so
   correctness is nearly free.
2. **Hue carries type, fill carries trust.** The parent §10 says trust is for
   colouring and §11.4 says colour is by type. Splitting the channels satisfies
   both instead of picking one.
3. **Materialized `contradicts` edges are excluded from degree.** The parent
   says to materialize them for display and does not say how they count. Left
   ambiguous, the natural reading inflates degree, and degree is node size.

## 9. Testing

`wiki_graph` is pure and tests without a substrate. Frontend rules from
`NEXT.md` §3 hold: `fireEvent` (`@testing-library/user-event` is not a
dependency), queries scoped by role or title, never a bare `getByText` in a
dense view. The unit suite never launches an agent.

Phase 3 produced eight tests that passed without ever exercising their target.
The habit that caught them: **proving a test can fail is not enough — the break
you substitute must be the specific mistake the test exists to catch.** The
tests most at risk here are the ones covering the decisions above, so each is
named with the defect it must catch:

| Test | Defect it must catch |
|---|---|
| degree excludes materialized edges | count them — both ends of one `contradicts` inflate |
| cache invalidation | edit content keeping byte size identical — must rebuild (fails under the parent's `mtime,size` key, which is the point) |
| layout determinism | drop our own seeding and rely on d3's internal LCG unseeded per-node — coincident starting nodes must still resolve, not fall into d3's `isNaN` fallback |
| filters do not manufacture orphans | mark filtered-isolated nodes orphan — a ring appears where the data has none |
| route resolution | visit `/wiki/graph` — must render the graph, not a page named "graph" (declaration order is not the mechanism under test; v6 resolves this by specificity regardless of where `/wiki/graph` sits relative to the catch-all) |
| lens toggle preserves layout | recompute positions on toggle — positions must be identical across lenses |
| source page has no neighbourhood | return graph neighbours for a Source page — must show citing pages instead |

## 10. Carried in from phase 3

One ruling from 2026-08-28 lands in this phase's code rather than its own:
**a human-accepted merge must appear in the Activity list.**
`Service.accept_wiki_merge` writes no `state["runs"]` entry today, so the audit
trail omits merges a human approved. This amends the parent §11.3, which defines
Activity as what *runs* did. The entry must mark itself as a human action, not a
run, so the view does not imply an agent did it. Do it before building further
UI on `state["runs"]`.
