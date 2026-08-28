import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type WikiGraphNode } from "../api";
import { forceLayout } from "../components/graphLayout";
import { nodeStyle, edgeStyle, nodeSize, type Lens } from "../components/wikiGraphStyle";

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

  if (q.isLoading) return <div className="wiki-graph">Loading the graph…</div>;
  if (q.isError) return <div className="wiki-graph">Could not load the graph.</div>;

  const byId = new Map<string, WikiGraphNode>((q.data?.nodes ?? []).map((n) => [n.id, n]));

  return (
    <div className="wiki-graph">
      <svg role="img" aria-label="concept graph" width="100%" height="640">
        {(q.data?.edges ?? []).map((e) => {
          const a = placed.find((p) => p.id === e.src);
          const b = placed.find((p) => p.id === e.dst);
          if (!a || !b) return null;
          const s = edgeStyle(e, lens);
          return (
            <line key={e.id} x1={a.position.x + 400} y1={a.position.y + 320}
                  x2={b.position.x + 400} y2={b.position.y + 320}
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
            <circle key={p.id} cx={p.position.x + 400} cy={p.position.y + 320}
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
