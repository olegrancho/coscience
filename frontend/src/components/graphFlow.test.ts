import { describe, it, expect } from "vitest";
import { toFlow, stageColor, edgeStyle } from "./graphFlow";
import type { GraphEdge } from "../api";

const edge = (over: Partial<GraphEdge>): GraphEdge => ({
  id: "e1", type: "builds_on", src: "s2", dst: "s1", source: "pm",
  by: "pm", at: 0, rationale: "r", confidence: "", evidence: "", ...over,
});

describe("stageColor", () => {
  it("gives distinct colors per stage", () => {
    const c = new Set([stageColor("idea"), stageColor("experiment"), stageColor("result")]);
    expect(c.size).toBe(3);
  });
});

describe("edgeStyle", () => {
  it("marks evidential edges dashed, lineage solid", () => {
    expect(edgeStyle(edge({ type: "confirms" })).dashed).toBe(true);
    expect(edgeStyle(edge({ type: "builds_on" })).dashed).toBe(false);
  });
});

describe("toFlow", () => {
  it("maps nodes and edges with correct direction", () => {
    const g = {
      nodes: [
        { id: "s1", kind: "experiment" as const, stage: "result" as const, label: "base" },
        { id: "s2", kind: "experiment" as const, stage: "experiment" as const, label: "next" },
      ],
      edges: [edge({})],
    };
    const { nodes, edges } = toFlow(g);
    expect(nodes.map((n) => n.id)).toEqual(["s1", "s2"]);
    expect(nodes[0].data.label).toBe("base");
    expect(edges).toHaveLength(1);
    expect([edges[0].source, edges[0].target]).toEqual(["s2", "s1"]);   // src -> dst
    expect(edges[0].data.edge.type).toBe("builds_on");
  });
});
