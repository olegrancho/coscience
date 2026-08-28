import { describe, it, expect } from "vitest";
import { neighbourhood } from "./wikiNeighbourhood";
import type { WikiGraphT } from "../api";

const n = (id: string) => ({
  id, slug: id, title: id.toUpperCase(), type: "Concept" as const,
  status: "draft", trust: "unverified", in_degree: 1, out_degree: 1,
  orphan: false, cluster: 0,
});
const e = (src: string, dst: string) => ({
  id: `${src}->${dst}`, src, dst, type: "refines", confidence: "high",
  source: "s", typed: true, materialized: false,
});
const g: WikiGraphT = { nodes: ["a", "b", "c", "d"].map(n),
                        edges: [e("a", "b"), e("b", "c"), e("c", "d")] };

describe("neighbourhood", () => {
  it("one hop is the centre and its immediate neighbours", () => {
    const out = neighbourhood(g, "b", 1);
    expect(out.nodes.map((x) => x.id).sort()).toEqual(["a", "b", "c"]);
  });

  it("two hops reaches one step further", () => {
    const out = neighbourhood(g, "b", 2);
    expect(out.nodes.map((x) => x.id).sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("direction is ignored — an inbound neighbour counts", () => {
    expect(neighbourhood(g, "b", 1).nodes.map((x) => x.id)).toContain("a");
  });

  it("an unknown centre yields an empty graph rather than throwing", () => {
    expect(neighbourhood(g, "sources/x.md", 1)).toEqual({ nodes: [], edges: [] });
  });
});
