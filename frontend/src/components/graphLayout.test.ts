import { describe, it, expect } from "vitest";
import { layout } from "./graphLayout";
import type { FlowNode, FlowEdge } from "./graphFlow";

const node = (id: string): FlowNode => ({
  id, data: { label: id, stage: "experiment", kind: "experiment" },
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
