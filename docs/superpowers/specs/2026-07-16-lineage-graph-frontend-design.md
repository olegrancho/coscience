# Lineage Graph — Frontend View Design

**Date:** 2026-07-16
**Status:** approved for planning
**Depends on:** the backend lineage graph (`2026-07-16-research-lineage-graph-design.md`,
merged branch `feat/lineage-graph`). Consumes `GET /programs/{id}/graph`.
**Scope:** phase 5 of the backend spec — the human-facing visualization. Read-only
(no edge editing in this pass).

## 1. Goal

Render a program's lineage graph (idea/experiment/result nodes + typed edges) as a
compact inline card on the program page, expandable to a full-window view. Humans
navigate the graph and read edge details; they do not create/delete edges yet
(the `POST`/`DELETE /programs/{id}/edges` endpoints exist and are wired later).

## 2. Decisions (locked)

- **Read-only v1.** View, navigate, hover for details. Edge editing is a follow-up.
- **React Flow + dagre.** `@xyflow/react` (React Flow 12) for rendering/pan/zoom;
  `dagre` for a layered directed auto-layout. First code-split in the app.
- **Inline card, expandable to full window.** A small "Lineage" card in the
  ProgramDetail stack; an expand control opens a Mantine `Modal fullScreen` with
  the same graph.

## 3. Architecture

### 3.1 Components

- **`LineageGraph`** (`frontend/src/components/LineageGraph.tsx`) — the reusable
  React Flow canvas. Props: `{ nodes, edges }` (the API payload) + optional
  `onNodeClick`. Owns: graph→React-Flow transform, dagre layout, node/edge
  styling, the legend, pan/zoom. No data fetching inside (dumb component).
- **`LineageCard`** (in `ProgramDetail.tsx` or its own file) — the inline
  container: a Mantine `Card` (~320px tall) that fetches the graph, renders
  `LineageGraph`, shows the empty-state placeholder, and holds the expand control
  + the `Modal fullScreen` that re-renders `LineageGraph` larger.

`LineageGraph` is rendered in both the card and the modal — one render path.

### 3.2 Lazy loading

React Flow is heavy and would be the app's first code-split. `LineageGraph` is
imported via `React.lazy(() => import("../components/LineageGraph"))`, wrapped in
`<Suspense fallback={<Loader/>}>`. The `@xyflow/react` + `dagre` chunk therefore
loads only when a program page actually renders a non-empty graph.

**Empty-graph gate:** `LineageCard` calls `getGraph` first; if `nodes` is empty it
renders a muted placeholder ("No lineage yet — the PM records edges as the program
develops.") and does **not** mount `LineageGraph`, so the chunk is never fetched
for programs with no graph.

### 3.3 Pure, testable units (extracted from LineageGraph)

To keep logic testable without a canvas (jsdom can't measure React Flow):

- **`toFlow(graph): { nodes: FlowNode[], edges: FlowEdge[] }`** — maps the API
  payload to React Flow nodes/edges, applying color/style. Pure.
- **`layout(flowNodes, flowEdges): FlowNode[]`** — runs dagre, returns nodes with
  `{x, y}` positions. Pure (dagre is headless; no DOM).
- **Style maps** — `stageColor(stage)`, `edgeStyle(edge)` (family → solid/dashed,
  source → color). Pure.

These three are unit-tested; the React Flow render is verified manually on the dev
instance.

## 4. Data

- **API:** `api.getGraph(id)` → `GET /api/programs/{id}/graph`, returns
  `{ nodes: GraphNode[], edges: GraphEdge[] }`.
  - `GraphNode = { id: string; kind: "idea"|"experiment"; stage: "idea"|"experiment"|"result"; label: string }`
  - `GraphEdge = { id, type, src, dst, source, by, at, rationale, confidence, evidence }`
- **Types** added to `frontend/src/api.ts` (`Graph`, `GraphNode`, `GraphEdge`) +
  the `getGraph` function (matching the existing `fetch(...).then(j<T>)` pattern).
- **Fetching:** `useQuery(["graph", id], () => api.getGraph(id))`. No
  `refetchInterval` — edges change slowly; refetch on mount is enough. (The card
  may `invalidateQueries(["graph", id])` opportunistically, but no polling.)

## 5. Visual encoding

- **Nodes** colored by **stage**: idea / experiment / result (done). Label is the
  truncated node `label`. React Flow default node with a stage-colored border/fill.
  - **Click:** sprint/result node → `nav(/sprints/{id})`; idea node →
    `nav(/programs/{programId}/ideas)` (ideas have no dedicated detail page).
- **Edges** styled by **family** (lineage = solid, evidential = dashed) and
  **source** (system / pm / human) by color. Directed marker (arrow) src→dst
  (node → its antecedent).
  - **Hover:** tooltip with `type`, `rationale`, `confidence` (if any), `by`.
- **Legend:** a small static legend (stage swatches + lineage/evidential line
  styles). Shown in both card and modal (compact in the card).

Exact palette is chosen at implementation time to match the app's Mantine theme
(reuse existing CSS vars / theme colors; no new global styles beyond what the
component needs).

## 6. Layout

- **dagre**, layered/directed. Edge direction src→dst (a node points to its
  antecedent). `rankdir` top-to-bottom (TB) by default; a node's rank follows the
  lineage depth. Evidential edges participate in the same graph but their cycles
  (allowed by the backend) must not break the layout — dagre tolerates cycles by
  breaking them for layout only; no correctness impact.
- Layout runs in `layout()` on every data change; positions are not persisted.
- `fitView` on mount and on expand so the whole graph is visible in both sizes.

## 7. Placement / interaction

- **Inline:** a "Lineage" `Card` added to `ProgramDetail`'s vertical `Stack`
  (same `cardStyle` as the other cards), fixed height (~320px). Header row: title
  + expand `ActionIcon` (⛶). Pan/zoom enabled; minimap off in the small card.
- **Expanded:** clicking expand opens a Mantine `Modal` with `fullScreen`,
  rendering `LineageGraph` at full height + the legend (and optionally a minimap).
  Close returns to the page. Same query data (no refetch needed).

## 8. Testing

- **Unit (vitest + testing-library):** `toFlow` (node/edge mapping incl. color +
  dash/source style), `layout` (dagre produces positions for every node; stable
  for a fixed input), `stageColor`/`edgeStyle` maps, and the empty-state gate
  (empty graph → placeholder, `LineageGraph` not mounted). No canvas needed.
- **Manual:** the React Flow render, pan/zoom, expand modal, node navigation, and
  edge hover tooltips are verified on the dev instance (`~/coscience-dev`, :8001).
  jsdom cannot measure React Flow, so the canvas itself is out of automated scope.

## 9. Out of scope (follow-ups)

- Human edge create/delete in the graph (the endpoints exist; a later pass adds the
  edge-draw UI + type/rationale form).
- Experiment-outcome tinting (confirmed/refuted node accents).
- Minimap/controls tuning, saved layouts, cross-program graphs.
- Enabling the Extended edge tier (backend follow-up; the frontend already renders
  whatever types the API returns, so no frontend change needed when it lands).

## 10. Dependencies / deploy

- Add `@xyflow/react`, `dagre`, `@types/dagre` (dev) to `frontend/package.json`.
- Deploy flow unchanged: `npm run build` always (bakes the new chunk). Hard-reload
  the dashboard after deploy. Runtime stays Linux-only for the backend; the
  frontend build runs on the host as today.
