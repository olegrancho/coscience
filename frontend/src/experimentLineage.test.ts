import { describe, expect, it } from "vitest";
import { experimentLineage } from "./experimentLineage";
import type { Graph } from "./api";

const node = (id: string, kind: "idea" | "experiment" = "experiment") =>
  ({ id, kind, stage: kind, label: `title of ${id}`, status: kind === "idea" ? "" : "done" });
const edge = (id: string, type: string, src: string, dst: string) =>
  ({ id, type, src, dst, source: "pm", by: "pm", at: 0, rationale: "", confidence: "", evidence: "" });

describe("an experiment's lineage (N1)", () => {
  const graph: Graph = {
    nodes: [node("s1"), node("s2"), node("s3"), node("i1", "idea")],
    edges: [edge("e1", "builds_on", "s2", "s1"), edge("e2", "inspired_by", "s2", "i1"),
            edge("e3", "refutes", "s3", "s2"), edge("e4", "builds_on", "s3", "s1")],
  };

  it("reads where it came from off its own edges, and what followed off edges to it", () => {
    const l = experimentLineage(graph, "s2");
    expect(l.from.map((x) => [x.verb, x.node.id])).toEqual([["builds on", "s1"], ["inspired by", "i1"]]);
    expect(l.followed.map((x) => [x.verb, x.node.id])).toEqual([["refutes this", "s3"]]);
  });

  it("names a node once, with every way it is linked", () => {
    const l = experimentLineage({ ...graph, edges: [...graph.edges, edge("e5", "confirms", "s3", "s1")] }, "s1");
    expect(l.followed.map((x) => [x.verb, x.node.id]))
      .toEqual([["builds on this", "s2"], ["builds on this · confirms this", "s3"]]);
  });

  it("is empty with no graph yet, or for a node nothing touches", () => {
    expect(experimentLineage(undefined, "s2")).toEqual({ from: [], followed: [] });
    expect(experimentLineage({ nodes: [node("x")], edges: [] }, "x")).toEqual({ from: [], followed: [] });
  });
});
