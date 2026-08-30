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
/** `rx`/`ry` make the ring an ellipse. A neighbourhood pane lives in a rail
 *  that is narrower than it is tall once labels are allowed for, and a circle
 *  spends its width on the corners; squashing the ring horizontally buys the
 *  labels their room back. Both default to a circle of `RING`. */
export function radialLayout(
  centreId: string, nodes: FlowNode[], rx: number = RING, ry: number = rx,
): FlowNode[] {
  const others = nodes.filter((nd) => nd.id !== centreId);
  const step = others.length ? (2 * Math.PI) / others.length : 0;
  let i = 0;
  return nodes.map((nd) => {
    if (nd.id === centreId) return { ...nd, position: { x: 0, y: 0 } };
    const a = step * i++ - Math.PI / 2;
    return { ...nd, position: { x: rx * Math.cos(a), y: ry * Math.sin(a) } };
  });
}
