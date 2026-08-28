import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type WikiGraphT } from "../api";
import { forceLayout } from "../components/graphLayout";
import { nodeStyle, edgeStyle, nodeSize, type Lens } from "../components/wikiGraphStyle";
import { applyFilters, emptyFilters, type Filters } from "../components/wikiGraphFilter";

// Padding around the laid-out bounding box, big enough to clear the largest
// node's radius (nodeSize tops out at 14 + 26 = 40, so a radius of 20).
const NODE_PAD = 60;

const EMPTY_GRAPH: WikiGraphT = { nodes: [], edges: [] };

function toggle(set: Set<string>, v: string): Set<string> {
  const next = new Set(set);
  if (next.has(v)) next.delete(v); else next.add(v);
  return next;
}

export default function WikiGraphView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [lens, setLens] = useState<Lens>("structure");
  const q = useQuery({ queryKey: ["wiki-graph", id], queryFn: () => api.getWikiGraph(id) });
  const [filters, setFilters] = useState<Filters>(emptyFilters());

  // Filtering hides; it never re-layouts (design 5.3). `placed` and `viewBox`
  // are both derived from the UNFILTERED graph so toggling a checkbox never
  // moves a node the reader is already looking at, or resizes the canvas
  // under them — only what's rendered from `shown` below changes.
  const placed = useMemo(() => {
    if (!q.data) return [];
    const flow = q.data.nodes.map((n) => ({
      id: n.id, data: { label: n.title, stage: "", kind: "", status: n.status },
      position: { x: 0, y: 0 }, style: {},
    }));
    const links = q.data.edges.map((e) => ({
      id: e.id, source: e.src, target: e.dst, label: e.type,
      data: { edge: e as never }, animated: false, style: {},
    }));
    return forceLayout(flow, links);
  }, [q.data]);

  // forceLayout's charge/collide forces push nodes wherever they need to go
  // to avoid overlap — at 50-200 nodes (spec 5.4) that lands well outside any
  // fixed canvas. A fixed width/height with no viewBox would just clip them;
  // fitting the viewBox to the actual laid-out bounding box (plus enough pad
  // to clear the largest node's radius) scales the view to fit instead.
  const viewBox = useMemo(() => {
    if (!placed.length) return "0 0 800 640";
    const xs = placed.map((p) => p.position.x);
    const ys = placed.map((p) => p.position.y);
    const minX = Math.min(...xs) - NODE_PAD;
    const minY = Math.min(...ys) - NODE_PAD;
    const w = Math.max(Math.max(...xs) - minX + NODE_PAD, 200);
    const h = Math.max(Math.max(...ys) - minY + NODE_PAD, 200);
    return `${minX} ${minY} ${w} ${h}`;
  }, [placed]);

  // Checkbox options come from the unfiltered graph so ticking one filter
  // never makes another filter's own choices disappear.
  const options = useMemo(() => {
    const g = q.data ?? EMPTY_GRAPH;
    return {
      types: Array.from(new Set(g.nodes.map((n) => n.type))).sort(),
      trust: Array.from(new Set(g.nodes.map((n) => n.trust))).sort(),
      relations: Array.from(new Set(g.edges.filter((e) => e.typed && e.type).map((e) => e.type))).sort(),
    };
  }, [q.data]);

  // `shown` is what's rendered; `placed`/`viewBox` above stay unfiltered so
  // hiding a node or edge never moves the rest or resizes the canvas.
  const shown = useMemo(() => applyFilters(q.data ?? EMPTY_GRAPH, filters), [q.data, filters]);

  if (q.isLoading) return <div className="wiki-graph">Loading the graph…</div>;
  if (q.isError) return <div className="wiki-graph">Could not load the graph.</div>;

  const placedById = new Map(placed.map((p) => [p.id, p]));

  return (
    <div className="wiki-graph">
      <p className="eyebrow">
        showing {shown.nodes.length} of {q.data?.nodes.length ?? 0} nodes
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
      <svg role="img" aria-label="concept graph" width="100%" height="640" viewBox={viewBox}>
        {shown.edges.map((e) => {
          const a = placedById.get(e.src);
          const b = placedById.get(e.dst);
          if (!a || !b) return null;
          const s = edgeStyle(e, lens);
          return (
            <line key={e.id} x1={a.position.x} y1={a.position.y}
                  x2={b.position.x} y2={b.position.y}
                  stroke={s.stroke} strokeWidth={Number(s.strokeWidth)}
                  strokeDasharray={s.strokeDasharray || undefined}
                  opacity={Number(s.opacity)} />
          );
        })}
        {shown.nodes.map((n) => {
          const p = placedById.get(n.id);
          if (!p) return null;
          const st = nodeStyle(n, lens);
          return (
            // A `<title>` nested inside `<circle>` is invisible to
            // testing-library's byTitle query (it only matches `svg > title`
            // — a title element that is a DIRECT child of the svg itself —
            // or an element with a `title` attribute), so the per-node label
            // is a `title` attribute here. It's a valid global attribute
            // (browsers render it as a native hover tooltip on any element,
            // SVG included) that React's SVGProps typing simply omits, hence
            // the cast.
            <circle key={p.id} cx={p.position.x} cy={p.position.y}
                    r={nodeSize(n) / 2}
                    fill={st.background === "transparent" ? "none" : st.background}
                    stroke={st.borderColor} strokeWidth={2}
                    opacity={Number(st.opacity)}
                    style={{ cursor: "pointer" }}
                    {...{ title: n.title }}
                    onClick={() => nav(`/programs/${id}/wiki/${n.slug}`)} />
          );
        })}
      </svg>
    </div>
  );
}
