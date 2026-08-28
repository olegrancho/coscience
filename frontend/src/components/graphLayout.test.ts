import { describe, it, expect } from "vitest";
import { layout, forceLayout, radialLayout } from "./graphLayout";
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

describe("forceLayout", () => {
  it("is deterministic — the same graph lays out identically every time", () => {
    const nodes = ["a", "b", "c", "d"].map(n);
    const edges = [e("a", "b"), e("b", "c"), e("c", "d")];
    const first = forceLayout(nodes, edges).map((x) => x.position);
    const second = forceLayout(nodes, edges).map((x) => x.position);
    expect(second).toEqual(first);
  });

  it("separates unconnected nodes via the simulation's repulsion, not just the seed", () => {
    // The phyllotaxis seed alone already spaces 3 nodes ~17-26px apart (no
    // ticks needed for that much). 150 sits well above what seeding alone
    // produces, and well above what forceCollide's local anti-overlap alone
    // achieves once forceManyBody is removed (~71px, measured) — so this
    // threshold is only reachable when the charge force actually runs for
    // the full tick count. See task-6-report.md fix-round-1 log for the
    // measured numbers behind 150.
    const out = forceLayout(["a", "b", "c"].map(n), []);
    const pts = out.map((x) => x.position);
    const dist = (p: { x: number; y: number }, q: { x: number; y: number }) =>
      Math.hypot(p.x - q.x, p.y - q.y);
    const pairs: Array<[number, number]> = [[0, 1], [0, 2], [1, 2]];
    const minPairwiseDistance = Math.min(...pairs.map(([i, j]) => dist(pts[i], pts[j])));
    expect(minPairwiseDistance).toBeGreaterThan(150);
  });
});

describe("radialLayout", () => {
  it("puts the centre at the origin and the rest on a ring around it", () => {
    const out = radialLayout("a", ["a", "b", "c", "d"].map(n));
    const at = (id: string) => out.find((x) => x.id === id)!.position;
    expect(at("a")).toEqual({ x: 0, y: 0 });
    const r = (p: { x: number; y: number }) => Math.round(Math.hypot(p.x, p.y));
    expect(r(at("b"))).toBe(r(at("c")));
    expect(r(at("b"))).toBeGreaterThan(0);
  });

  it("is deterministic", () => {
    const nodes = ["a", "b", "c"].map(n);
    expect(radialLayout("a", nodes)).toEqual(radialLayout("a", nodes));
  });
});
