import type { FlowNode } from "./graphFlow";

const RING = 120;

/** A one-hop neighbourhood is under ten nodes, so no simulation is needed.
 *  Lives in its own module, separate from forceLayout/layout in
 *  graphLayout.ts, because Rollup chunks per FILE, not per export: sharing a
 *  module with forceLayout's d3-force import (or layout()'s dagre import)
 *  would drag both into whatever bundle reaches this function — which is the
 *  eager browse-view chunk (WikiNeighbourhood is statically reachable from
 *  App -> WikiView). This split is what actually keeps d3-force and dagre
 *  out of that chunk; tree-shaking alone cannot separate two live exports of
 *  the same module. */
export function radialLayout(centreId: string, nodes: FlowNode[]): FlowNode[] {
  const others = nodes.filter((nd) => nd.id !== centreId);
  const step = others.length ? (2 * Math.PI) / others.length : 0;
  let i = 0;
  return nodes.map((nd) => {
    if (nd.id === centreId) return { ...nd, position: { x: 0, y: 0 } };
    const a = step * i++ - Math.PI / 2;
    return { ...nd, position: { x: RING * Math.cos(a), y: RING * Math.sin(a) } };
  });
}
