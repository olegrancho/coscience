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

  it("separates unconnected nodes rather than stacking them at the origin", () => {
    const out = forceLayout(["a", "b", "c"].map(n), []);
    const keys = new Set(out.map((x) => `${Math.round(x.position.x)},${Math.round(x.position.y)}`));
    expect(keys.size).toBe(3);
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
