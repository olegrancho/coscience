import type { WikiGraphT } from "../api";

/** The centre plus everything within `hops` edges, direction ignored.
 *  Serves both the browse view's pane and the full view's focus mode, so the
 *  two can never disagree about what a neighbourhood is. */
export function neighbourhood(g: WikiGraphT, centreId: string, hops: number): WikiGraphT {
  if (!g.nodes.some((n) => n.id === centreId)) return { nodes: [], edges: [] };
  let reached = new Set([centreId]);
  for (let i = 0; i < hops; i++) {
    const next = new Set(reached);
    for (const e of g.edges) {
      if (reached.has(e.src)) next.add(e.dst);
      if (reached.has(e.dst)) next.add(e.src);
    }
    reached = next;
  }
  return {
    nodes: g.nodes.filter((n) => reached.has(n.id)),
    edges: g.edges.filter((e) => reached.has(e.src) && reached.has(e.dst)),
  };
}
