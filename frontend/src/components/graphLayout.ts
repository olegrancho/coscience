import dagre from "dagre";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide }
  from "d3-force";
import type { FlowNode, FlowEdge } from "./graphFlow";

const NODE_W = 160;
const NODE_H = 44;

export function layout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 60 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) {
    g.setNode(n.id, { width: n.width ?? NODE_W, height: n.height ?? NODE_H });
  }
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);   // dagre breaks cycles internally for layout; no throw
  return nodes.map((n) => {
    const p = g.node(n.id);
    const w = n.width ?? NODE_W;
    const h = n.height ?? NODE_H;
    return { ...n, position: { x: p.x - w / 2, y: p.y - h / 2 } };
  });
}

const FORCE_TICKS = 300;
const RING = 120;

/** Deterministic seed positions: d3's phyllotaxis, without its random jiggle.
 *  d3-force perturbs coincident nodes with Math.random, so an unseeded run
 *  draws a different picture every visit and the graph never becomes a shape
 *  you can learn. Seeding + a fixed tick count makes it reproducible. */
function seed(i: number): { x: number; y: number } {
  const r = 10 * Math.sqrt(0.5 + i);
  const a = i * Math.PI * (3 - Math.sqrt(5));
  return { x: r * Math.cos(a), y: r * Math.sin(a) };
}

export function forceLayout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
  const sim = nodes.map((nd, i) => ({ id: nd.id, ...seed(i) }));
  const links = edges
    .filter((ed) => ed.source !== ed.target)
    .map((ed) => ({ source: ed.source, target: ed.target }));
  forceSimulation(sim as never[])
    .force("link", forceLink(links as never[]).id((d: unknown) => (d as { id: string }).id).distance(90))
    .force("charge", forceManyBody().strength(-240))
    .force("centre", forceCenter(0, 0))
    .force("collide", forceCollide(28))
    .stop()
    .tick(FORCE_TICKS);
  const at = new Map(sim.map((s) => [s.id, s]));
  return nodes.map((nd) => {
    const p = at.get(nd.id);
    return { ...nd, position: { x: p?.x ?? 0, y: p?.y ?? 0 } };
  });
}

/** A one-hop neighbourhood is under ten nodes, so no simulation is needed —
 *  and this keeps d3-force off the browse view's bundle entirely. */
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
