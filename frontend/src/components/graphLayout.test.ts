import { describe, it, expect } from "vitest";
import { layout } from "./graphLayout";
import type { FlowNode, FlowEdge } from "./graphFlow";

const node = (id: string): FlowNode => ({
  id, data: { label: id, stage: "experiment", kind: "experiment", status: "proposed" },
  position: { x: 0, y: 0 }, style: {},
});
const edge = (s: string, t: string): FlowEdge => ({
  id: `${s}-${t}`, source: s, target: t, label: "builds_on",
  data: { edge: {} as any }, animated: false, style: {},
});

describe("layout", () => {
  it("assigns a position to every node", () => {
    const out = layout([node("a"), node("b")], [edge("a", "b")]);
    expect(out).toHaveLength(2);
    for (const n of out) {
      expect(typeof n.position.x).toBe("number");
      expect(typeof n.position.y).toBe("number");
    }
  });

  it("separates connected nodes into different ranks (different y)", () => {
    const out = layout([node("a"), node("b")], [edge("a", "b")]);
    const ys = out.map((n) => n.position.y);
    expect(ys[0]).not.toBe(ys[1]);
  });

  it("tolerates a cycle without throwing", () => {
    expect(() => layout([node("a"), node("b")], [edge("a", "b"), edge("b", "a")])).not.toThrow();
  });
});

const n = (id: string): FlowNode => ({
  id, data: { label: id, stage: "", kind: "", status: "" },
  position: { x: 0, y: 0 }, style: {},
});
const e = (source: string, target: string): FlowEdge => ({
  id: `${source}->${target}`, source, target, label: "",
  data: { edge: {} as never }, animated: false, style: {},
});

const typed = (source: string, target: string, type: string): FlowEdge => ({
  id: `${source}-${type}-${target}`, source, target, label: type,
  data: { edge: {} as never }, animated: false, style: {},
});
const box = (out: FlowNode[]) => {
  const xs = out.map((o) => o.position.x), ys = out.map((o) => o.position.y);
  return { w: Math.max(...xs) - Math.min(...xs) + 160, h: Math.max(...ys) - Math.min(...ys) + 44 };
};

describe("compact layout (D1)", () => {
  it("places by one parent per node, so long evidence edges do not widen every rank", () => {
    // A trunk of 30 experiments, each building on the last, with every one also
    // confirming the experiment ten back: drawn, but not what places them.
    const ids = Array.from({ length: 30 }, (_, i) => `s${i}`);
    const edges = ids.slice(1).map((id, i) => typed(id, ids[i], "builds_on"));
    for (let i = 10; i < 30; i++) edges.push(typed(ids[i], ids[i - 10], "confirms"));
    const { w } = box(layout(ids.map(n), edges));
    expect(w).toBeLessThan(400);          // one column, not a band
  });

  it("gathers nodes with no edges into a grid rather than one long row", () => {
    const { w, h } = box(layout(Array.from({ length: 16 }, (_, i) => n(`i${i}`)), []));
    expect(w / h).toBeLessThan(4);
  });

  it("packs separate clusters into rows instead of side by side", () => {
    const nodes: FlowNode[] = [], edges: FlowEdge[] = [];
    for (let c = 0; c < 8; c++) {
      for (let k = 0; k < 4; k++) nodes.push(n(`c${c}-${k}`));
      for (let k = 1; k < 4; k++) edges.push(typed(`c${c}-${k}`, `c${c}-${k - 1}`, "builds_on"));
    }
    const { w, h } = box(layout(nodes, edges));
    expect(w / h).toBeLessThan(4);        // side by side, eight of them would be ~12 wide per 1 tall
    expect(new Set(layout(nodes, edges).map((o) => o.position.x)).size).toBeGreaterThan(1);
  });
});
