import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type WikiGraphNode } from "../api";
import { forceLayout } from "../components/graphLayout";
import { nodeStyle, edgeStyle, nodeSize, type Lens } from "../components/wikiGraphStyle";

// Padding around the laid-out bounding box, big enough to clear the largest
// node's radius (nodeSize tops out at 14 + 26 = 40, so a radius of 20).
const NODE_PAD = 60;

export default function WikiGraphView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const lens: Lens = "structure";
  const q = useQuery({ queryKey: ["wiki-graph", id], queryFn: () => api.getWikiGraph(id) });

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

  if (q.isLoading) return <div className="wiki-graph">Loading the graph…</div>;
  if (q.isError) return <div className="wiki-graph">Could not load the graph.</div>;

  const byId = new Map<string, WikiGraphNode>((q.data?.nodes ?? []).map((n) => [n.id, n]));

  return (
    <div className="wiki-graph">
      <svg role="img" aria-label="concept graph" width="100%" height="640" viewBox={viewBox}>
        {(q.data?.edges ?? []).map((e) => {
          const a = placed.find((p) => p.id === e.src);
          const b = placed.find((p) => p.id === e.dst);
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
        {placed.map((p) => {
          const n = byId.get(p.id);
          if (!n) return null;
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
