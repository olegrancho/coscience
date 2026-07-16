# Lineage Graph Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a program's lineage graph as an inline card on the program page, expandable to a full-window view (read-only).

**Architecture:** A reusable `LineageGraph` React Flow component fed by pure, unit-tested transform (`toFlow`) and layout (`dagre`) modules; wrapped by a `LineageCard` that fetches `GET /programs/:id/graph`, gates the empty state, and hosts the expand modal. React Flow is lazy-loaded (the app's first code-split).

**Tech Stack:** React 18, Mantine 7, TanStack Query v5, react-router v6, Vite 5, vitest; new deps `@xyflow/react` (React Flow 12) + `dagre`.

**Spec:** `docs/superpowers/specs/2026-07-16-lineage-graph-frontend-design.md`

## Global Constraints

- **Read-only v1** — no edge create/delete UI. Render, navigate, hover only.
- **Pure modules never import `@xyflow/react`** — `toFlow`/`layout`/style maps use locally-defined `FlowNode`/`FlowEdge` types so they run under vitest (jsdom cannot measure React Flow). Only `LineageGraph.tsx` imports React Flow.
- **React Flow is lazy-loaded** via `React.lazy` + `Suspense`; an empty graph renders a placeholder and never mounts `LineageGraph` (chunk not fetched).
- **API shape (already served by backend):** `GET /api/programs/{id}/graph` → `{ nodes: {id,kind,stage,label}[], edges: {id,type,src,dst,source,by,at,rationale,confidence,evidence}[] }`. `kind ∈ {idea,experiment}`, `stage ∈ {idea,experiment,result}`.
- **Node click nav:** sprint/result node → `/sprints/{id}`; idea node → `/programs/{programId}/ideas`.
- **Deploy unchanged:** `npm run build` always. Frontend build + tests run on the Linux host (`~/coscience-dev/frontend`, `PATH=$HOME/node20/bin:$PATH`), NOT on the Windows dev box.
- **Never commit or push without explicit user approval** (project rule).

### Host commands (controller runs these; subagents are write-only)

- Sync a changed frontend file: `scp <local> aish-sandbox:coscience-dev/<same-path>`
- Install deps after a package.json change: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm install'`
- Run a vitest file: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npx vitest run <file>'`
- Typecheck + build: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm run build'`

---

### Task 1: Add deps + API types + `getGraph`

**Files:**
- Modify: `frontend/package.json` (dependencies)
- Modify: `frontend/src/api.ts` (types + `getGraph`)

**Interfaces:**
- Produces: `Graph`, `GraphNode`, `GraphEdge` interfaces and `api.getGraph(id: string): Promise<Graph>`.

- [ ] **Step 1: Add dependencies**

In `frontend/package.json`, add to `dependencies`:

```json
    "@xyflow/react": "^12.3.0",
    "dagre": "^0.8.5",
```

and to `devDependencies`:

```json
    "@types/dagre": "^0.7.52",
```

- [ ] **Step 2: Install on the host**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm install'`
Expected: installs `@xyflow/react`, `dagre`, `@types/dagre` with no peer-dependency errors (React 18 is compatible with React Flow 12).

- [ ] **Step 3: Add types + `getGraph` to `api.ts`**

Add these interfaces near the other exported interfaces at the top of `frontend/src/api.ts`:

```ts
export interface GraphNode {
  id: string;
  kind: "idea" | "experiment";
  stage: "idea" | "experiment" | "result";
  label: string;
}

export interface GraphEdge {
  id: string;
  type: string;
  src: string;
  dst: string;
  source: string;      // "system" | "pm" | "human"
  by: string;
  at: number;
  rationale: string;
  confidence: string;  // "" | "low" | "med" | "high"
  evidence: string;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
```

Add this entry to the `api` object literal (next to `getProgram`/`listIdeas`, matching the existing `fetch(...).then(j<T>)` style):

```ts
  getGraph: (id: string) => fetch(`/api/programs/${id}/graph`).then(j<Graph>),
```

- [ ] **Step 4: Typecheck + build on host**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm run build'`
Expected: `tsc -b` passes and `vite build` succeeds (types compile; no unused-symbol errors).

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/api.ts
git commit -m "feat(graph-ui): add react-flow/dagre deps + getGraph API client"
```

(Note: `package-lock.json` is regenerated on the host by `npm install`; scp it back before committing, or commit it from the host checkout — coordinate at execution time.)

---

### Task 2: Pure graph→flow transform + style maps

**Files:**
- Create: `frontend/src/components/graphFlow.ts`
- Test: `frontend/src/components/graphFlow.test.ts`

**Interfaces:**
- Consumes: `GraphNode`, `GraphEdge` from `../api`.
- Produces:
  - `FlowNode = { id: string; data: { label: string; stage: string; kind: string }; position: { x: number; y: number }; style: Record<string, unknown> }`
  - `FlowEdge = { id: string; source: string; target: string; label: string; data: { edge: GraphEdge }; animated: boolean; style: Record<string, unknown> }`
  - `stageColor(stage: string): string`
  - `edgeStyle(edge: GraphEdge): { style: Record<string, unknown>; dashed: boolean }`
  - `toFlow(graph: { nodes: GraphNode[]; edges: GraphEdge[] }): { nodes: FlowNode[]; edges: FlowEdge[] }` — maps API payload to flow nodes/edges (positions default to `{x:0,y:0}`; layout comes later). React Flow edge direction: `source = edge.src`, `target = edge.dst`.

This module MUST NOT import `@xyflow/react` (keeps it vitest-safe).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/graphFlow.test.ts
import { describe, it, expect } from "vitest";
import { toFlow, stageColor, edgeStyle } from "./graphFlow";
import type { GraphEdge } from "../api";

const edge = (over: Partial<GraphEdge>): GraphEdge => ({
  id: "e1", type: "builds_on", src: "s2", dst: "s1", source: "pm",
  by: "pm", at: 0, rationale: "r", confidence: "", evidence: "", ...over,
});

describe("stageColor", () => {
  it("gives distinct colors per stage", () => {
    const c = new Set([stageColor("idea"), stageColor("experiment"), stageColor("result")]);
    expect(c.size).toBe(3);
  });
});

describe("edgeStyle", () => {
  it("marks evidential edges dashed, lineage solid", () => {
    expect(edgeStyle(edge({ type: "confirms" })).dashed).toBe(true);
    expect(edgeStyle(edge({ type: "builds_on" })).dashed).toBe(false);
  });
});

describe("toFlow", () => {
  it("maps nodes and edges with correct direction", () => {
    const g = {
      nodes: [
        { id: "s1", kind: "experiment" as const, stage: "result" as const, label: "base" },
        { id: "s2", kind: "experiment" as const, stage: "experiment" as const, label: "next" },
      ],
      edges: [edge({})],
    };
    const { nodes, edges } = toFlow(g);
    expect(nodes.map((n) => n.id)).toEqual(["s1", "s2"]);
    expect(nodes[0].data.label).toBe("base");
    expect(edges).toHaveLength(1);
    expect([edges[0].source, edges[0].target]).toEqual(["s2", "s1"]);   // src -> dst
    expect(edges[0].data.edge.type).toBe("builds_on");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npx vitest run src/components/graphFlow.test.ts'`
Expected: FAIL — module `./graphFlow` not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/components/graphFlow.ts
import type { GraphNode, GraphEdge } from "../api";

export interface FlowNode {
  id: string;
  data: { label: string; stage: string; kind: string };
  position: { x: number; y: number };
  style: Record<string, unknown>;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  data: { edge: GraphEdge };
  animated: boolean;
  style: Record<string, unknown>;
}

const STAGE_COLORS: Record<string, string> = {
  idea: "#8a8f98",         // gray — a candidate direction
  experiment: "#3b82f6",   // blue — running/pending work
  result: "#16a34a",       // green — a finished experiment
};

const SOURCE_COLORS: Record<string, string> = {
  system: "#8a8f98",
  pm: "#3b82f6",
  human: "#a855f7",
};

const EVIDENTIAL = new Set(["confirms", "refutes", "contradicts"]);

export function stageColor(stage: string): string {
  return STAGE_COLORS[stage] ?? "#8a8f98";
}

export function edgeStyle(edge: GraphEdge): { style: Record<string, unknown>; dashed: boolean } {
  const dashed = EVIDENTIAL.has(edge.type);
  const color = SOURCE_COLORS[edge.source] ?? "#8a8f98";
  return {
    dashed,
    style: { stroke: color, strokeWidth: 1.5, strokeDasharray: dashed ? "6 4" : undefined },
  };
}

export function toFlow(graph: { nodes: GraphNode[]; edges: GraphEdge[] }): {
  nodes: FlowNode[];
  edges: FlowEdge[];
} {
  const nodes: FlowNode[] = graph.nodes.map((n) => ({
    id: n.id,
    data: { label: n.label || n.id, stage: n.stage, kind: n.kind },
    position: { x: 0, y: 0 },
    style: {
      border: `2px solid ${stageColor(n.stage)}`,
      borderRadius: 8,
      padding: "6px 10px",
      fontSize: 12,
      background: "var(--mantine-color-body, #fff)",
    },
  }));
  const edges: FlowEdge[] = graph.edges.map((e) => {
    const { style } = edgeStyle(e);
    return {
      id: e.id,
      source: e.src,
      target: e.dst,
      label: e.type,
      data: { edge: e },
      animated: false,
      style,
    };
  });
  return { nodes, edges };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npx vitest run src/components/graphFlow.test.ts'`
Expected: PASS (3 describes).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/graphFlow.ts frontend/src/components/graphFlow.test.ts
git commit -m "feat(graph-ui): pure graph->flow transform + stage/edge style maps"
```

---

### Task 3: dagre layout

**Files:**
- Create: `frontend/src/components/graphLayout.ts`
- Test: `frontend/src/components/graphLayout.test.ts`

**Interfaces:**
- Consumes: `FlowNode`, `FlowEdge` from `./graphFlow`.
- Produces: `layout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[]` — returns the nodes with dagre-computed `{x, y}` positions (top-to-bottom). Pure; `dagre` is headless (no DOM).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/graphLayout.test.ts
import { describe, it, expect } from "vitest";
import { layout } from "./graphLayout";
import type { FlowNode, FlowEdge } from "./graphFlow";

const node = (id: string): FlowNode => ({
  id, data: { label: id, stage: "experiment", kind: "experiment" },
  position: { x: 0, y: 0 }, style: {},
});
const edge = (s: string, t: string): FlowEdge => ({
  id: `${s}-${t}`, source: s, target: t, label: "builds_on",
  data: { edge: {} as any }, animated: false, style: {},
});

describe("layout", () => {
  it("assigns a position to every node", () => {
    const out = layout([node("a"), node("b")], [edge("a", "b")]);
    expect(out).toHaveLength(2);
    for (const n of out) {
      expect(typeof n.position.x).toBe("number");
      expect(typeof n.position.y).toBe("number");
    }
  });

  it("separates connected nodes into different ranks (different y)", () => {
    const out = layout([node("a"), node("b")], [edge("a", "b")]);
    const ys = out.map((n) => n.position.y);
    expect(ys[0]).not.toBe(ys[1]);
  });

  it("tolerates a cycle without throwing", () => {
    expect(() => layout([node("a"), node("b")], [edge("a", "b"), edge("b", "a")])).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npx vitest run src/components/graphLayout.test.ts'`
Expected: FAIL — module `./graphLayout` not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/components/graphLayout.ts
import dagre from "dagre";
import type { FlowNode, FlowEdge } from "./graphFlow";

const NODE_W = 160;
const NODE_H = 44;

export function layout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 60 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);   // dagre breaks cycles internally for layout; no throw
  return nodes.map((n) => {
    const p = g.node(n.id);
    return { ...n, position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } };
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npx vitest run src/components/graphLayout.test.ts'`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/graphLayout.ts frontend/src/components/graphLayout.test.ts
git commit -m "feat(graph-ui): dagre layered layout for the lineage graph"
```

---

### Task 4: `LineageGraph` React Flow component

**Files:**
- Create: `frontend/src/components/LineageGraph.tsx`
- Manual verification only (React Flow needs a real DOM; jsdom can't measure it).

**Interfaces:**
- Consumes: `toFlow` (Task 2), `layout` (Task 3), `Graph` from `../api`.
- Produces: default-exported `LineageGraph` component. Props:
  `{ graph: Graph; onNodeClick?: (nodeId: string) => void }`. Renders a React Flow canvas (pan/zoom, `fitView`, arrow markers, edge hover title) filling its parent (parent sets the height). Default export so it can be `React.lazy`-imported.

- [ ] **Step 1: Implement the component**

```tsx
// frontend/src/components/LineageGraph.tsx
import { useMemo } from "react";
import { ReactFlow, Background, Controls, MarkerType } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Graph } from "../api";
import { toFlow } from "./graphFlow";
import { layout } from "./graphLayout";

export default function LineageGraph({
  graph,
  onNodeClick,
}: {
  graph: Graph;
  onNodeClick?: (nodeId: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const flow = toFlow(graph);
    const positioned = layout(flow.nodes, flow.edges);
    const edgesWithMarker = flow.edges.map((e) => ({
      ...e,
      markerEnd: { type: MarkerType.ArrowClosed },
      // Native title tooltip on hover: type, rationale, confidence, author.
      label: e.label,
      labelStyle: { fontSize: 10 },
      data: e.data,
    }));
    return { nodes: positioned, edges: edgesWithMarker };
  }, [graph]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={true}
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, node) => onNodeClick?.(node.id)}
    >
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
```

- [ ] **Step 2: Sync + build on host**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm run build'`
Expected: build succeeds; a separate chunk containing `@xyflow/react` appears in the Vite output (confirms code-split readiness).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/LineageGraph.tsx
git commit -m "feat(graph-ui): LineageGraph react-flow canvas (layout + markers + click)"
```

---

### Task 5: `LineageCard` inline + expand modal, wired into ProgramDetail

**Files:**
- Create: `frontend/src/components/LineageCard.tsx`
- Modify: `frontend/src/views/ProgramDetail.tsx` (render `LineageCard` in the stack)
- Manual verification on the dev instance.

**Interfaces:**
- Consumes: `api.getGraph` (Task 1), `LineageGraph` (Task 4, lazy).
- Produces: default-exported `LineageCard` component. Props: `{ programId: string }`. Fetches the graph, renders the empty-state placeholder when there are no nodes (without mounting `LineageGraph`), otherwise renders `LineageGraph` in a ~320px card with an expand control that opens a `Modal fullScreen` rendering the same graph.

- [ ] **Step 1: Implement `LineageCard`**

```tsx
// frontend/src/components/LineageCard.tsx
import { Suspense, lazy, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ActionIcon, Card, Group, Loader, Modal, Text, Tooltip } from "@mantine/core";
import { api } from "../api";

const LineageGraph = lazy(() => import("./LineageGraph"));

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function LineageCard({ programId }: { programId: string }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const graph = useQuery({ queryKey: ["graph", programId], queryFn: () => api.getGraph(programId) });

  const go = (nodeId: string) => {
    const n = graph.data?.nodes.find((x) => x.id === nodeId);
    if (!n) return;
    if (n.kind === "idea") nav(`/programs/${programId}/ideas`);
    else nav(`/sprints/${nodeId}`);
  };

  const hasGraph = !!graph.data && graph.data.nodes.length > 0;

  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" mb="xs">
        <Text fw={600}>lineage</Text>
        {hasGraph && (
          <Tooltip label="Expand">
            <ActionIcon variant="subtle" onClick={() => setOpen(true)} aria-label="Expand graph">⛶</ActionIcon>
          </Tooltip>
        )}
      </Group>

      {!graph.data ? (
        <Loader size="sm" />
      ) : !hasGraph ? (
        <Text c="dimmed" size="sm">
          No lineage yet — the PM records edges (inspired_by, builds_on, confirms/refutes)
          as the program develops.
        </Text>
      ) : (
        <div style={{ height: 320 }}>
          <Suspense fallback={<Loader size="sm" />}>
            <LineageGraph graph={graph.data} onNodeClick={go} />
          </Suspense>
        </div>
      )}

      <Modal opened={open} onClose={() => setOpen(false)} fullScreen title="Program lineage">
        {hasGraph && (
          <div style={{ height: "80vh" }}>
            <Suspense fallback={<Loader size="sm" />}>
              <LineageGraph graph={graph.data!} onNodeClick={(id) => { setOpen(false); go(id); }} />
            </Suspense>
          </div>
        )}
      </Modal>
    </Card>
  );
}
```

- [ ] **Step 2: Render `LineageCard` in ProgramDetail**

In `frontend/src/views/ProgramDetail.tsx`: import the component near the other imports —
`import LineageCard from "../components/LineageCard";` — and add it to the vertical
`<Stack>` (place it just before or after the "experiments" card):

```tsx
      <LineageCard programId={id} />
```

(`id` is the program id already read via `useParams` in this view.)

- [ ] **Step 3: Sync + build on host**

Run: `ssh aish-sandbox 'cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm run build'`
Expected: build succeeds.

- [ ] **Step 4: Manual verification on the dev instance**

Restart/refresh the dev frontend (`~/coscience-dev`, :8001; the http server serves `frontend/dist` static — a rebuild + hard-reload is enough, no server restart needed). Seed at least one program with a couple of edges (via `POST /api/programs/{id}/edges`, or the PM), then via the ssh tunnel confirm:
- A program with no edges shows the "No lineage yet" placeholder (and the React Flow chunk is NOT fetched — check the network tab).
- A program with edges shows the inline graph (~320px), nodes colored by stage, lineage edges solid / evidential dashed, arrows src→dst.
- Clicking a sprint/result node navigates to `/sprints/{id}`; clicking an idea node navigates to the ideas view.
- The expand icon opens a full-screen modal with the same graph; close returns to the page.
- Hovering an edge shows its type/label.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LineageCard.tsx frontend/src/views/ProgramDetail.tsx
git commit -m "feat(graph-ui): inline lineage card with expand-to-fullscreen on the program page"
```

---

## Self-Review

**Spec coverage:**
- §3.1 components → `LineageGraph` (Task 4) + `LineageCard` (Task 5).
- §3.2 lazy load + empty-state gate → Task 5 (`lazy`, `Suspense`, `hasGraph` gate).
- §3.3 pure units → `toFlow`/styles (Task 2), `layout` (Task 3), both vitest-tested.
- §4 data/API → Task 1 (`getGraph` + types), Task 5 (`useQuery`).
- §5 visual encoding → Task 2 (stage color, family dash, source color), Task 4 (arrow markers, hover label), Task 5 (node-click nav).
- §6 layout → Task 3 (dagre TB, cycle-tolerant).
- §7 placement/interaction → Task 5 (inline card + fullscreen modal).
- §8 testing → Tasks 2/3 unit; Tasks 4/5 manual (documented why: jsdom can't measure React Flow).
- §10 deps/deploy → Task 1.

**Placeholder scan:** none — every code step has full code; every run step names the exact host command + expected result. Manual-verification steps (Tasks 4/5) are explicitly manual per the spec's testing section, with concrete checks listed.

**Type consistency:** `FlowNode`/`FlowEdge` defined in Task 2, imported unchanged in Tasks 3/4. `toFlow`/`layout`/`stageColor`/`edgeStyle` signatures identical across tasks. `Graph`/`GraphNode`/`GraphEdge` defined in Task 1, used in Tasks 2/4/5. `getGraph(id) → Promise<Graph>` consistent. `LineageGraph` props `{graph, onNodeClick}` match between Task 4 (definition) and Task 5 (usage).

**One risk flagged:** `package-lock.json` is generated on the host by `npm install` (Task 1). To keep the lockfile in git it must be scp'd back from the host (or committed from the host checkout). Noted in Task 1 Step 5.
