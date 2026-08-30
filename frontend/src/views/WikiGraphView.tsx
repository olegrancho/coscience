import { Group, Text } from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ReactFlow, Background, BaseEdge, Controls, EdgeLabelRenderer, MarkerType,
  Handle, Position, useStore,
  type Node, type NodeProps, type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type WikiGraphT, type WikiGraphNode } from "../api";
import { createWikiSim, type WikiSim } from "../components/wikiGraphSim";
import { rimSegment, segmentPath, toCorner, toCentre } from "../components/discGeometry";
import { nodeStyle, edgeStyle, nodeSize, TENSION_TYPES, type Lens }
  from "../components/wikiGraphStyle";
import {
  applyFilters, emptyFilters, filterOptions, resolveFilters, toggleOption,
  type Filters,
} from "../components/wikiGraphFilter";
import { GraphFilters } from "../components/WikiGraphControls";
import { neighbourhood } from "../components/wikiNeighbourhood";
import { loadPositions, savePositions, clearPositions } from "../components/graphPositions";
import { BackLink } from "../components/ui";

const EMPTY_GRAPH: WikiGraphT = { nodes: [], edges: [] };
const LABEL_ZOOM = 0.6;
/** Label size in SCREEN pixels. A label styled in flow coordinates grows with
 *  the zoom, so `fitView` on a small graph — which zooms in — rendered names
 *  bigger than the nodes and piled them on top of each other. Dividing by the
 *  zoom pins them to a constant on-screen size instead. */
const LABEL_PX = 11;
/** Below this alpha the simulation has stopped rearranging things. */
const SETTLED = 0.05;
/** Relation names are detail, not structure. They are drawn at a constant
 *  on-screen size like the node labels, and only once the reader is close
 *  enough for them to be worth the clutter. */
const EDGE_LABEL_PX = 10;
const EDGE_LABEL_ZOOM = 0.85;

type ConceptData = {
  node: WikiGraphNode;
  /** Disc diameter, computed once by the view so the node, the edge trimming
   *  and the drag maths all agree on one number. */
  r: number;
  lens: Lens;
  inTension: boolean;
  onOpen: () => void;
};

/** React Flow anchors an edge wherever it measures the handle inside the node
 *  box. Pinning both handles to the middle of the disc is what makes an edge
 *  leave the node; left at their defaults they land at the middle of the
 *  disc-plus-label strip, which is inside the label text. */
const HANDLE_AT_CENTRE = {
  left: "50%", top: "50%", right: "auto", bottom: "auto",
  transform: "translate(-50%, -50%)", visibility: "hidden",
} as const;

/** A node is a coloured disc. Its title floats beside the disc but is NOT part
 *  of the node's box — that is what keeps the disc, and so the handles, at the
 *  node's position. The label hides when zoomed out, the same trick
 *  LineageGraph's DotNode uses, so a dense graph stays legible. The `title`
 *  attribute (not a nested <title> element) carries the hover tooltip and is
 *  what tests query by. */
function ConceptNode({ data }: NodeProps) {
  const d = data as ConceptData;
  const zoom = useStore((s) => s.transform[2]);
  const st = nodeStyle(d.node, d.lens, d.inTension);
  const r = d.r;
  return (
    <div style={{ position: "relative", width: r, height: r, opacity: Number(st.opacity) }}>
      <Handle type="target" position={Position.Top} style={HANDLE_AT_CENTRE} />
      <span
        {...{ title: d.node.title }}
        onClick={d.onOpen}
        style={{
          display: "block", boxSizing: "border-box", width: r, height: r,
          borderRadius: "50%", cursor: "pointer",
          background: st.background, border: `2px solid ${st.borderColor}`,
          outline: st.outline || undefined, outlineOffset: 2,
        }}
      />
      {zoom >= LABEL_ZOOM && (
        <span style={{
          position: "absolute", left: r + 6 / zoom, top: "50%",
          transform: "translateY(-50%)",
          fontSize: LABEL_PX / zoom, whiteSpace: "nowrap", pointerEvents: "none",
          color: "var(--ink, #222)", textDecoration: st.textDecoration,
        }}>
          {d.node.title}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} style={HANDLE_AT_CENTRE} />
    </div>
  );
}

type RimData = { sourceR: number; targetR: number };

/** A straight line between two discs' rims. The built-in edge types draw a
 *  bezier between handles, which with centred handles would loop out and back
 *  and bury its arrowhead under the target disc; this draws the line a force
 *  graph wants and stops it where the node starts. Geometry lives in
 *  discGeometry.ts, which is testable — jsdom renders no React Flow edges. */
function RimEdge(
  { sourceX, sourceY, targetX, targetY, markerStart, markerEnd, style, label, data }: EdgeProps,
) {
  const zoom = useStore((st) => st.transform[2]);
  const d = data as RimData | undefined;
  const seg = rimSegment(sourceX, sourceY, targetX, targetY, d?.sourceR ?? 0, d?.targetR ?? 0);
  if (!seg) return null;
  return (
    <>
      <BaseEdge path={segmentPath(seg)} markerStart={markerStart} markerEnd={markerEnd}
                style={style} />
      {label && zoom >= EDGE_LABEL_ZOOM && (
        <EdgeLabelRenderer>
          <div style={{
            position: "absolute", pointerEvents: "none",
            fontSize: EDGE_LABEL_PX / zoom, lineHeight: 1.4,
            padding: `0 ${3 / zoom}px`, borderRadius: 3, whiteSpace: "nowrap",
            background: "var(--paper, #f1f4f2)", color: "var(--ink-faint, #8b9a94)",
            opacity: Number(style?.opacity ?? 1),
            transform: `translate(-50%, -50%) translate(${(seg.x1 + seg.x2) / 2}px, ${
              (seg.y1 + seg.y2) / 2}px)`,
          }}>
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { concept: ConceptNode };
const edgeTypes = { rim: RimEdge };

export default function WikiGraphView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus") ?? "";
  const [lens, setLens] = useState<Lens>("structure");
  const [filters, setFilters] = useState<Filters>(emptyFilters());
  const q = useQuery({ queryKey: ["wiki-graph", id], queryFn: () => api.getWikiGraph(id) });

  // Focus mode reduces to a neighbourhood around one node before anything else
  // runs — the simulation, filtering and the count all read `source`, never
  // `q.data`, so a focused view is indistinguishable from having loaded a
  // smaller graph in the first place.
  const source = useMemo(() => {
    if (!q.data) return undefined;
    const nid = q.data.nodes.find((n) => n.id.replace(/\.md$/, "") === focus)?.id;
    return nid ? neighbourhood(q.data, nid, 2) : q.data;
  }, [q.data, focus]);

  // The live simulation. It is rebuilt only when the underlying graph changes —
  // never when a filter or the lens changes, so hiding an edge or repainting
  // for tension leaves the arrangement exactly where the reader left it. That
  // is the live-simulation form of the old "filtering never re-layouts" rule.
  // Built during render, not in an effect. An effect runs after the first
  // paint, so the first render would place every node at (0,0) — and React
  // Flow's fitView, which runs on mount, would fit that degenerate box and
  // leave the graph zoomed wrong once the nodes spread out.
  const [, bump] = useState(0);
  const sim = useMemo(() => {
    if (!source) return null;
    return createWikiSim(
      source.nodes.map((n) => n.id),
      source.edges.map((e) => ({ source: e.src, target: e.dst })),
      { pinned: loadPositions(id, "wiki-graph") },
    );
  }, [source, id]);

  const simRef = useRef<WikiSim | null>(null);
  simRef.current = sim;
  // Just the one call we make; the full instance type is parameterised by the
  // node and edge shapes and does not survive being widened to a ref.
  const flow = useRef<{ fitView: (o?: { duration?: number; padding?: number }) => void } | null>(null);

  useEffect(() => {
    if (!sim) return;
    let frame = 0;
    // React Flow's own `fitView` runs on mount, when every node is still sitting
    // on its seed position — a tight little cluster. It fits THAT, which means
    // it zooms a long way in, and then the simulation spreads the graph out
    // underneath a viewport that never looks again. So fit once more when the
    // physics has actually stopped moving things.
    let refit = false;
    sim.onTick(() => {
      if (!refit && sim.alpha() < SETTLED) {
        refit = true;
        flow.current?.fitView({ duration: 400, padding: 0.18 });
      }
      // Coalesce to one React render per animation frame; d3 ticks faster than
      // the screen refreshes and re-rendering per tick wastes most of them.
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; bump((n) => n + 1); });
    });
    return () => { if (frame) cancelAnimationFrame(frame); sim.stop(); };
  }, [sim]);

  // Options come from the WHOLE graph, not the focused subset, so entering
  // focus mode never silently drops a control the reader had set.
  const options = useMemo(() => filterOptions(q.data ?? EMPTY_GRAPH), [q.data]);
  // An untouched group resolves to "all of it" here, once, and everything
  // downstream — the chips and the filtering — reads the same resolved sets.
  // That is what stops the controls disagreeing with the picture.
  const active = useMemo(() => resolveFilters(filters, options), [filters, options]);
  const shown = useMemo(() => applyFilters(source ?? EMPTY_GRAPH, active), [source, active]);

  // Which nodes touch a tension edge, so the lens can dim the rest.
  const inTension = useMemo(() => {
    const s = new Set<string>();
    for (const e of shown.edges) {
      if (TENSION_TYPES.has(e.type)) { s.add(e.src); s.add(e.dst); }
    }
    return s;
  }, [shown]);

  const pos = sim?.nodes() ?? [];
  const posById = new Map(pos.map((p) => [p.id, p]));

  // The simulation thinks in centres; React Flow places a node by its top-left
  // corner. Shifting by half the disc keeps the two in the same coordinates —
  // without it the disc, and so every edge anchored to it, sits down and right
  // of where the physics put the node.
  const radius = new Map(shown.nodes.map((n) => [n.id, nodeSize(n) / 2]));

  const flowNodes: Node[] = shown.nodes.map((n) => {
    const p = posById.get(n.id);
    const r = nodeSize(n);
    return {
      id: n.id,
      type: "concept",
      position: toCorner({ x: p?.x ?? 0, y: p?.y ?? 0 }, r),
      data: {
        node: n, r, lens, inTension: inTension.has(n.id),
        onOpen: () => nav(`/programs/${id}/wiki/${n.id.replace(/\.md$/, "")}`),
      } satisfies ConceptData,
    };
  });

  // A materialized `contradicts` reverse is the same disagreement as its
  // forward edge. Drawing both stacks two lines on identical endpoints at
  // double alpha; drawing one with a head at each end says it once.
  const flowEdges = shown.edges
    .filter((e) => !e.materialized)
    .map((e) => {
      const s = edgeStyle(e, lens);
      const twoWay = e.type === "contradicts";
      return {
        id: e.id,
        source: e.src,
        target: e.dst,
        type: "rim",
        data: { sourceR: radius.get(e.src) ?? 0, targetR: radius.get(e.dst) ?? 0 } satisfies RimData,
        label: e.typed ? e.type : undefined,
        markerEnd: { type: MarkerType.ArrowClosed, color: s.stroke },
        ...(twoWay ? { markerStart: { type: MarkerType.ArrowClosed, color: s.stroke } } : {}),
        style: {
          stroke: s.stroke,
          strokeWidth: Number(s.strokeWidth),
          strokeDasharray: s.strokeDasharray || undefined,
          opacity: Number(s.opacity),
        },
      };
    });

  const onDragStart = useCallback((_: unknown, n: Node) => {
    simRef.current?.dragStart(n.id);
  }, []);
  const onDrag = useCallback((_: unknown, n: Node) => {
    // Undo the half-disc shift applied when placing the node, so the physics
    // is told where the disc's centre now is, not its corner.
    const c = toCentre(n.position, (n.data as unknown as ConceptData).r);
    simRef.current?.dragTo(n.id, c.x, c.y);
  }, []);
  const onDragStop = useCallback((_: unknown, n: Node) => {
    const sim = simRef.current;
    if (!sim) return;
    sim.dragEnd(n.id);
    savePositions(id, sim.pinned(), "wiki-graph");
  }, [id]);

  const resetLayout = useCallback(() => {
    clearPositions(id, "wiki-graph");
    simRef.current?.unpinAll();
  }, [id]);

  const setGroup = (g: "types" | "relations" | "trust", v: string) =>
    setFilters((f) => ({ ...f, [g]: toggleOption(active[g], v) }));
  const allGroup = (g: "types" | "relations" | "trust") =>
    setFilters((f) => ({ ...f, [g]: null }));

  if (q.isLoading) return <div className="wiki-graph">Loading the graph…</div>;
  if (q.isError) return <div className="wiki-graph">Could not load the graph.</div>;

  return (
    <div className="wiki-graph">
      {/* The graph is a view OF the wiki, not a place of its own: without this
          the only way back was the browser's back button. Same idiom as the
          maintenance log's header. */}
      <BackLink to={`/programs/${id}/wiki`}>Wiki</BackLink>
      <Group justify="space-between" align="baseline" wrap="nowrap" mb="xs">
        <Text fw={600} size="xl">Concept graph</Text>
        <Text size="xs" c="dimmed">
          showing {shown.nodes.length} of {source?.nodes.length ?? 0} nodes
          {focus && (
            <>
              {" — "}
              <a href="#" onClick={(e) => {
                e.preventDefault();
                setParams((p) => { const next = new URLSearchParams(p); next.delete("focus"); return next; });
              }}>
                show whole graph
              </a>
            </>
          )}
        </Text>
      </Group>

      <GraphFilters
        options={options}
        shown={active}
        onToggle={setGroup}
        onAll={allGroup}
        typedOnly={filters.typedOnly}
        onTypedOnly={(v) => setFilters((f) => ({ ...f, typedOnly: v }))}
        tension={lens === "tension"}
        onTension={(v) => setLens(v ? "tension" : "structure")}
        onReset={resetLayout}
      />

      <div role="img" aria-label="concept graph" style={{ width: "100%", height: 640 }}>
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeDragStart={onDragStart}
          onNodeDrag={onDrag}
          onNodeDragStop={onDragStop}
          nodesDraggable
          nodesConnectable={false}
          onInit={(i) => { flow.current = i; }}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
