import { describe, it, expect } from "vitest";
import { radialLayout } from "./radialLayout";
import type { FlowNode } from "./graphFlow";

const n = (id: string): FlowNode => ({
  id, data: { label: id, stage: "", kind: "", status: "" },
  position: { x: 0, y: 0 }, style: {},
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
