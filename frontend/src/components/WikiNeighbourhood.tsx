import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { neighbourhood } from "./wikiNeighbourhood";
import { radialLayout } from "./graphLayout";
import { nodeStyle, nodeSize } from "./wikiGraphStyle";

const BOX = 240, HALF = BOX / 2;

/** The browse view's neighbourhood pane. Deliberately laid out with
 *  `radialLayout`, never `forceLayout` — a one-hop neighbourhood is under ten
 *  nodes, and keeping d3-force off the browse view's bundle is the whole
 *  reason the radial layout exists (design 6).
 *
 *  `pageType` is optional and comes from data the caller (WikiView) already
 *  has loaded for its own page tree — passing it costs no extra fetch. It
 *  lets the "not in the graph" message be honest about *why*:
 *    - "Source" (a real page, just a type the concept graph excludes by
 *      design) gets the specific, confident explanation.
 *    - `""` (the caller looked and found no page at that address at all)
 *      gets a "no such page" message instead.
 *    - `undefined` (the caller didn't look, e.g. this component used on its
 *      own) falls back to a message that doesn't assert a reason it can't
 *      know — better silent about the cause than confidently wrong. */
export default function WikiNeighbourhood(
  { programId, slug, pageType }: { programId: string; slug: string; pageType?: string },
) {
  const q = useQuery({ queryKey: ["wiki-graph", programId],
                       queryFn: () => api.getWikiGraph(programId) });
  const centreId = useMemo(
    () => q.data?.nodes.find((n) => n.slug === slug)?.id ?? "",
    [q.data, slug]);
  const sub = useMemo(
    () => (q.data && centreId ? neighbourhood(q.data, centreId, 1) : { nodes: [], edges: [] }),
    [q.data, centreId]);
  const placed = useMemo(
    () => radialLayout(centreId, sub.nodes.map((n) => ({
      id: n.id, data: { label: n.title, stage: "", kind: "", status: n.status },
      position: { x: 0, y: 0 }, style: {},
    }))),
    [sub, centreId]);

  if (q.isLoading) return null;
  if (!centreId) {
    const message = pageType === "Source"
      ? "This is a Source page — sources are excluded from the concept graph by design."
      : pageType === ""
      ? "No page was found at this address."
      : "This page is not in the concept graph.";
    return (
      <p className="muted" style={{ fontSize: 12 }}>{message}</p>
    );
  }

  const byId = new Map(sub.nodes.map((n) => [n.id, n]));
  return (
    <div>
      <svg role="img" aria-label="neighbourhood" width={BOX} height={BOX}>
        {sub.edges.map((e) => {
          const a = placed.find((p) => p.id === e.src);
          const b = placed.find((p) => p.id === e.dst);
          if (!a || !b) return null;
          return <line key={e.id} x1={a.position.x + HALF} y1={a.position.y + HALF}
                       x2={b.position.x + HALF} y2={b.position.y + HALF}
                       stroke="#8a8f98" strokeWidth={1} />;
        })}
        {placed.map((p) => {
          const n = byId.get(p.id);
          if (!n) return null;
          const st = nodeStyle(n, "structure");
          // A `title` attribute on the circle itself, not a nested `<title>`
          // child — byTitle's svg-title match requires the `<title>` to be a
          // DIRECT child of `<svg>`, which a per-node label nested inside a
          // `<circle>` never is (see WikiGraphView.tsx for the same fix).
          return (
            <circle key={p.id} cx={p.position.x + HALF} cy={p.position.y + HALF}
                    r={nodeSize(n) / 2}
                    fill={st.background === "transparent" ? "none" : st.background}
                    stroke={st.borderColor}
                    strokeWidth={p.id === centreId ? 3 : 2}
                    {...{ title: n.title }} />
          );
        })}
      </svg>
      <Link to={`/programs/${programId}/wiki/graph?focus=${slug}`}
            className="view" style={{ fontSize: 12 }}>
        open full graph ↗
      </Link>
    </div>
  );
}
