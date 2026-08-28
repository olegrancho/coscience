import { describe, it, expect } from "vitest";
import { applyFilters, emptyFilters } from "./wikiGraphFilter";
import type { WikiGraphT } from "../api";

const g: WikiGraphT = {
  nodes: [
    { id: "a", slug: "a", title: "A", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 0, out_degree: 1, orphan: false, cluster: 0 },
    { id: "b", slug: "b", title: "B", type: "Entity", status: "draft",
      trust: "human-reviewed", in_degree: 1, out_degree: 0, orphan: false, cluster: 0 },
  ],
  edges: [
    { id: "e1", src: "a", dst: "b", type: "refines", confidence: "high",
      source: "s", typed: true, materialized: false },
    { id: "e2", src: "a", dst: "b", type: "", confidence: "", source: "",
      typed: false, materialized: false },
  ],
};

it("typedOnly drops untyped body-link edges", () => {
  const out = applyFilters(g, { ...emptyFilters(), typedOnly: true });
  expect(out.edges.map((e) => e.id)).toEqual(["e1"]);
});

it("filtering by node type hides the node and its edges", () => {
  const out = applyFilters(g, { ...emptyFilters(), types: new Set(["Concept"]) });
  expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
  expect(out.edges).toEqual([]);
});

it("never manufactures an orphan", () => {
  // The defect this catches: recomputing `orphan` from the FILTERED edge set,
  // which rings a node whose edges the reader merely hid. `orphan` is a
  // whole-graph property computed server-side.
  const out = applyFilters(g, { ...emptyFilters(), relations: new Set(["is_a"]) });
  expect(out.edges).toEqual([]);
  expect(out.nodes.every((n) => n.orphan === false)).toBe(true);
});
