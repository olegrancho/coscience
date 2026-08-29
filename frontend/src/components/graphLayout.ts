import dagre from "dagre";
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
