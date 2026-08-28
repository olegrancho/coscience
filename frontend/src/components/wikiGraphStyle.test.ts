// frontend/src/components/wikiGraphStyle.test.ts
import { describe, it, expect } from "vitest";
import { nodeStyle, edgeStyle, nodeSize, TYPE_HUE } from "./wikiGraphStyle";
import type { WikiGraphNode, WikiGraphEdge } from "../api";

const node = (over: Partial<WikiGraphNode> = {}): WikiGraphNode => ({
  id: "concepts/a.md", slug: "a", title: "A", type: "Concept",
  status: "draft", trust: "unverified",
  in_degree: 0, out_degree: 0, orphan: true, cluster: 0, ...over,
});
const edge = (over: Partial<WikiGraphEdge> = {}): WikiGraphEdge => ({
  id: "e", src: "a", dst: "b", type: "refines", confidence: "high",
  source: "wt-r1", typed: true, materialized: false, ...over,
});

describe("node encoding", () => {
  it("uses hue for page type", () => {
    expect(nodeStyle(node({ type: "Concept" }), "structure").borderColor)
      .toBe(TYPE_HUE.Concept);
    expect(nodeStyle(node({ type: "Entity" }), "structure").borderColor)
      .toBe(TYPE_HUE.Entity);
    expect(TYPE_HUE.Concept).not.toBe(TYPE_HUE.Entity);
  });

  it("uses fill for trust, hollow through solid", () => {
    const hollow = nodeStyle(node({ trust: "unverified" }), "structure").background;
    const tinted = nodeStyle(node({ trust: "machine-confirmed" }), "structure").background;
    const solid = nodeStyle(node({ trust: "human-reviewed" }), "structure").background;
    expect(new Set([hollow, tinted, solid]).size).toBe(3);
    expect(hollow).toBe("transparent");
  });

  it("rings an orphan and only an orphan", () => {
    expect(nodeStyle(node({ orphan: true }), "structure").outline).toBeTruthy();
    expect(nodeStyle(node({ orphan: false }), "structure").outline).toBeFalsy();
  });

  it("dims a deprecated page", () => {
    const d = nodeStyle(node({ status: "deprecated" }), "structure");
    expect(Number(d.opacity)).toBeLessThan(1);
    expect(d.textDecoration).toBe("line-through");
  });

  it("sizes by degree, so a better-connected node is larger", () => {
    expect(nodeSize(node({ in_degree: 4, out_degree: 3 })))
      .toBeGreaterThan(nodeSize(node({ in_degree: 0, out_degree: 1 })));
  });
});

describe("edge encoding", () => {
  it("draws untyped links fainter than typed ones", () => {
    const typed = edgeStyle(edge({ typed: true }), "structure");
    const untyped = edgeStyle(edge({ typed: false, type: "" }), "structure");
    expect(untyped.strokeDasharray).toBeTruthy();
    expect(Number(untyped.opacity)).toBeLessThan(Number(typed.opacity));
  });

  it("tension lens makes contradicts loud and everything else quiet", () => {
    const contra = edgeStyle(edge({ type: "contradicts" }), "tension");
    const partOf = edgeStyle(edge({ type: "part_of" }), "tension");
    expect(Number(contra.strokeWidth)).toBeGreaterThan(Number(partOf.strokeWidth));
    expect(Number(partOf.opacity)).toBeLessThan(Number(contra.opacity));
  });

  it("structure lens does not single out contradicts", () => {
    const contra = edgeStyle(edge({ type: "contradicts" }), "structure");
    const partOf = edgeStyle(edge({ type: "part_of" }), "structure");
    expect(contra.strokeWidth).toBe(partOf.strokeWidth);
  });

  it("tension lens draws untyped edges fainter than typed quiet edges", () => {
    const typedQuiet = edgeStyle(edge({ type: "part_of", typed: true }), "tension");
    const untypedQuiet = edgeStyle(edge({ type: "part_of", typed: false }), "tension");
    expect(Number(untypedQuiet.opacity)).toBeLessThan(Number(typedQuiet.opacity));
  });
});
