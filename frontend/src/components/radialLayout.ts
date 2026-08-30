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
  ringOf?: (id: string) => number,
): FlowNode[] {
  // One ring per hop. Two hops out, everything on a single ring is a wall of
  // discs that says nothing about which of them the page is actually about;
  // separate rings put the direct relations nearest and read as distance.
  const ring = (id: string) => Math.max(1, ringOf ? ringOf(id) : 1);
  const members = new Map<number, string[]>();
  for (const nd of nodes) {
    if (nd.id === centreId) continue;
    const r = ring(nd.id);
    members.set(r, [...(members.get(r) ?? []), nd.id]);
  }
  const index = new Map<string, number>();
  for (const ids of members.values()) ids.forEach((id, i) => index.set(id, i));

  return nodes.map((nd) => {
    if (nd.id === centreId) return { ...nd, position: { x: 0, y: 0 } };
    const r = ring(nd.id);
    const peers = members.get(r)!.length;
    const step = peers ? (2 * Math.PI) / peers : 0;
    // Each ring starts at a different angle, so an outer node does not sit
    // directly behind the inner one it hangs off.
    const a = step * (index.get(nd.id) ?? 0) - Math.PI / 2 + (r - 1) * (step / 2);
    return { ...nd, position: { x: r * rx * Math.cos(a), y: r * ry * Math.sin(a) } };
  });
}
