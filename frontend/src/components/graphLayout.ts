import dagre from "dagre";
import type { FlowNode, FlowEdge } from "./graphFlow";

const NODE_W = 160;
const NODE_H = 44;
const GAP = 60;             // between packed clusters
// The shape the packed clusters aim for: the lineage card is wide and short.
const TARGET_ASPECT = 2.2;

// The edge a node is placed by, when it has several: the most direct descent first.
const LAYOUT_PREFERENCE = ["builds_on", "supersedes", "replicates", "refines", "follows",
  "inspired_by", "duplicate_of", "confirms", "refutes", "contradicts"];

const rank = (type: string) => {
  const i = LAYOUT_PREFERENCE.indexOf(type);
  return i < 0 ? LAYOUT_PREFERENCE.length : i;
};

/** D1. A program's lineage drawn with every edge as a dagre layer got very wide: an
 *  edge spanning several ranks is routed through each one it crosses, and with a few
 *  hundred edges that widened every rank (one program measured 7,200 x 2,600 px with no
 *  rank holding more than 13 nodes). So nodes are PLACED by one edge each — the most
 *  direct parent, a tree — and every edge is still DRAWN. Clusters that share no edge,
 *  and nodes with no edge at all, are laid out apart and packed into rows aimed at the
 *  card's shape instead of standing side by side. On the same program: about 4x less
 *  area, so the card shows it at twice the scale. */
export function layout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
  const ids = new Set(nodes.map((n) => n.id));
  const live = edges.filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target);

  // One placing edge per node: its outbound edge of the most direct kind.
  const placing = new Map<string, FlowEdge>();
  for (const e of live) {
    const cur = placing.get(e.source);
    if (!cur || rank(e.label) < rank(cur.label)) placing.set(e.source, e);
  }

  // Clusters by every edge, so nodes joined only by evidence still sit together.
  const parent = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  for (const e of live) parent.set(find(e.source), find(e.target));
  const groups = new Map<string, FlowNode[]>();
  const loose: FlowNode[] = [];
  const degree = new Map<string, number>();
  for (const e of live) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }
  for (const n of nodes) {
    if (!degree.get(n.id)) { loose.push(n); continue; }
    const r = find(n.id);
    groups.set(r, [...(groups.get(r) ?? []), n]);
  }

  // Lay out each cluster on its own, in its own coordinates.
  type Box = { w: number; h: number; at: Map<string, { x: number; y: number }> };
  const boxes: Box[] = [];
  for (const members of groups.values()) {
    const inside = new Set(members.map((n) => n.id));
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "TB", nodesep: 20, ranksep: 40 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const n of members) g.setNode(n.id, { width: n.width ?? NODE_W, height: n.height ?? NODE_H });
    for (const id of inside) {
      const e = placing.get(id);
      if (e && inside.has(e.target)) g.setEdge(e.source, e.target);
    }
    dagre.layout(g);   // dagre breaks cycles internally for layout; no throw
    boxes.push(normalise(members, (id) => g.node(id)));
  }
  // Nodes with no edge at all: a grid, as one more box to pack.
  if (loose.length) {
    const cols = Math.max(1, Math.ceil(Math.sqrt(loose.length * TARGET_ASPECT * (NODE_H + 20) / (NODE_W + 20))));
    const rowH = Math.max(...loose.map((n) => n.height ?? NODE_H)) + 20;
    boxes.push(normalise(loose, (id) => {
      const i = loose.findIndex((n) => n.id === id);
      const n = loose[i];
      const w = n.width ?? NODE_W, h = n.height ?? NODE_H;
      return { x: (i % cols) * (NODE_W + 20) + w / 2, y: Math.floor(i / cols) * rowH + h / 2, width: w, height: h };
    }));
  }

  // Shelf-pack the boxes, tallest first, into rows about as wide as the target shape.
  const area = boxes.reduce((a, b) => a + (b.w + GAP) * (b.h + GAP), 0);
  const rowWidth = Math.max(Math.sqrt(area * TARGET_ASPECT), ...boxes.map((b) => b.w));
  const placed = new Map<string, { x: number; y: number }>();
  let x = 0, y = 0, rowH = 0;
  for (const b of [...boxes].sort((p, q) => q.h - p.h)) {
    if (x > 0 && x + b.w > rowWidth) { y += rowH + GAP; x = 0; rowH = 0; }
    for (const [id, p] of b.at) placed.set(id, { x: x + p.x, y: y + p.y });
    x += b.w + GAP;
    rowH = Math.max(rowH, b.h);
  }
  return nodes.map((n) => ({ ...n, position: placed.get(n.id) ?? { x: 0, y: 0 } }));
}

/** Top-left positions for a group laid out around centres, shifted to start at 0,0. */
function normalise(members: FlowNode[],
                   centre: (id: string) => { x: number; y: number; width: number; height: number }) {
  const raw = members.map((n) => {
    const c = centre(n.id);
    return { id: n.id, x: c.x - c.width / 2, y: c.y - c.height / 2, w: c.width, h: c.height };
  });
  const x0 = Math.min(...raw.map((r) => r.x)), y0 = Math.min(...raw.map((r) => r.y));
  const at = new Map(raw.map((r) => [r.id, { x: r.x - x0, y: r.y - y0 }]));
  return {
    w: Math.max(...raw.map((r) => r.x + r.w)) - x0,
    h: Math.max(...raw.map((r) => r.y + r.h)) - y0,
    at,
  };
}
