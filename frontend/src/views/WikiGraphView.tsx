import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ReactFlow, Background, Controls, MarkerType, Handle, Position, useStore,
  type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type WikiGraphT, type WikiGraphNode } from "../api";
import { createWikiSim, type WikiSim } from "../components/wikiGraphSim";
import { nodeStyle, edgeStyle, nodeSize, TENSION_TYPES, type Lens }
  from "../components/wikiGraphStyle";
import { applyFilters, emptyFilters, type Filters } from "../components/wikiGraphFilter";
import { neighbourhood } from "../components/wikiNeighbourhood";
import { loadPositions, savePositions, clearPositions } from "../components/graphPositions";

const EMPTY_GRAPH: WikiGraphT = { nodes: [], edges: [] };
const LABEL_ZOOM = 0.6;

function toggle(set: Set<string>, v: string): Set<string> {
  const next = new Set(set);
  if (next.has(v)) next.delete(v); else next.add(v);
  return next;
}

type ConceptData = {
  node: WikiGraphNode;
  lens: Lens;
  inTension: boolean;
  onOpen: () => void;
};

/** A node is a coloured disc plus its title. The label hides when zoomed out,
 *  the same trick LineageGraph's DotNode uses, so a dense graph stays legible.
 *  The `title` attribute (not a nested <title> element) carries the hover
 *  tooltip and is what tests query by. */
function ConceptNode({ data }: NodeProps) {
  const d = data as ConceptData;
  const zoom = useStore((s) => s.transform[2]);
  const st = nodeStyle(d.node, d.lens, d.inTension);
  const r = nodeSize(d.node);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, opacity: Number(st.opacity) }}>
      <Handle type="target" position={Position.Top} style={{ visibility: "hidden" }} />
      <span
        {...{ title: d.node.title }}
        onClick={d.onOpen}
        style={{
          width: r, height: r, borderRadius: "50%", flex: "0 0 auto", cursor: "pointer",
          background: st.background, border: `2px solid ${st.borderColor}`,
          outline: st.outline || undefined, outlineOffset: 2,
        }}
      />
      {zoom >= LABEL_ZOOM && (
        <span style={{
          fontSize: 10, whiteSpace: "nowrap", pointerEvents: "none",
          color: "var(--ink, #222)", textDecoration: st.textDecoration,
        }}>
          {d.node.title}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />
    </div>
  );
}

const nodeTypes = { concept: ConceptNode };

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

  useEffect(() => {
    if (!sim) return;
    let frame = 0;
    sim.onTick(() => {
      // Coalesce to one React render per animation frame; d3 ticks faster than
      // the screen refreshes and re-rendering per tick wastes most of them.
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; bump((n) => n + 1); });
    });
    return () => { if (frame) cancelAnimationFrame(frame); sim.stop(); };
  }, [sim]);

  const options = useMemo(() => {
    const g = q.data ?? EMPTY_GRAPH;
    return {
      types: Array.from(new Set(g.nodes.map((n) => n.type))).sort(),
      trust: Array.from(new Set(g.nodes.map((n) => n.trust))).sort(),
      relations: Array.from(new Set(g.edges.filter((e) => e.typed && e.type).map((e) => e.type))).sort(),
    };
  }, [q.data]);

  const shown = useMemo(() => applyFilters(source ?? EMPTY_GRAPH, filters), [source, filters]);

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

  const flowNodes: Node[] = shown.nodes.map((n) => {
    const p = posById.get(n.id);
    return {
      id: n.id,
      type: "concept",
      position: { x: p?.x ?? 0, y: p?.y ?? 0 },
      data: {
        node: n, lens, inTension: inTension.has(n.id),
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
    simRef.current?.dragTo(n.id, n.position.x, n.position.y);
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

  if (q.isLoading) return <div className="wiki-graph">Loading the graph…</div>;
  if (q.isError) return <div className="wiki-graph">Could not load the graph.</div>;

  return (
    <div className="wiki-graph">
      <p className="eyebrow">
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
      </p>
      <fieldset>
        <legend>Type</legend>
        {options.types.map((t) => (
          <label key={t} style={{ marginRight: 12 }}>
            <input type="checkbox" aria-label={t} checked={filters.types.has(t)}
                   onChange={() => setFilters((f) => ({ ...f, types: toggle(f.types, t) }))} />
            {" "}{t}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Relation</legend>
        {options.relations.map((r) => (
          <label key={r} style={{ marginRight: 12 }}>
            <input type="checkbox" aria-label={r} checked={filters.relations.has(r)}
                   onChange={() => setFilters((f) => ({ ...f, relations: toggle(f.relations, r) }))} />
            {" "}{r}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Trust</legend>
        {options.trust.map((t) => (
          <label key={t} style={{ marginRight: 12 }}>
            <input type="checkbox" aria-label={t} checked={filters.trust.has(t)}
                   onChange={() => setFilters((f) => ({ ...f, trust: toggle(f.trust, t) }))} />
            {" "}{t}
          </label>
        ))}
      </fieldset>
      <label>
        <input type="checkbox" aria-label="typed only" checked={filters.typedOnly}
               onChange={(e) => setFilters((f) => ({ ...f, typedOnly: e.target.checked }))} />
        {" "}typed only
      </label>
      <label>
        <input type="checkbox" aria-label="tension"
               checked={lens === "tension"}
               onChange={(e) => setLens(e.target.checked ? "tension" : "structure")} />
        {" "}tension
      </label>
      <button type="button" onClick={resetLayout} style={{ marginLeft: 12 }}>
        reset layout
      </button>
      <div role="img" aria-label="concept graph" style={{ width: "100%", height: 640 }}>
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onNodeDragStart={onDragStart}
          onNodeDrag={onDrag}
          onNodeDragStop={onDragStop}
          nodesDraggable
          nodesConnectable={false}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
