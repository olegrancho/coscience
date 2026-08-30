import { describe, it, expect } from "vitest";
import { neighbourhood, oidForSourceSlug, fitLabel } from "./wikiNeighbourhood";
import type { WikiGraphT } from "../api";

// Bundle-shaped ids (a real page's path, e.g. "concepts/a.md"), never the
// bare slug — a flat "a"-style fixture would pass even if `neighbourhood`
// were compared against the wrong field.
const n = (slug: string) => ({
  id: `concepts/${slug}.md`, slug, title: slug.toUpperCase(), type: "Concept" as const,
  status: "draft", trust: "unverified" as const, in_degree: 1, out_degree: 1,
  orphan: false, cluster: 0,
});
const e = (srcSlug: string, dstSlug: string) => ({
  id: `${srcSlug}->${dstSlug}`, src: `concepts/${srcSlug}.md`, dst: `concepts/${dstSlug}.md`,
  type: "refines", confidence: "high", source: "s", typed: true, materialized: false,
});
const g: WikiGraphT = { nodes: ["a", "b", "c", "d"].map(n),
                        edges: [e("a", "b"), e("b", "c"), e("c", "d")] };

describe("neighbourhood", () => {
  it("one hop is the centre and its immediate neighbours", () => {
    const out = neighbourhood(g, "concepts/b.md", 1);
    expect(out.nodes.map((x) => x.id).sort()).toEqual(
      ["concepts/a.md", "concepts/b.md", "concepts/c.md"]);
  });

  it("two hops reaches one step further", () => {
    const out = neighbourhood(g, "concepts/b.md", 2);
    expect(out.nodes.map((x) => x.id).sort()).toEqual(
      ["concepts/a.md", "concepts/b.md", "concepts/c.md", "concepts/d.md"]);
  });

  it("direction is ignored — an inbound neighbour counts", () => {
    expect(neighbourhood(g, "concepts/b.md", 1).nodes.map((x) => x.id)).toContain("concepts/a.md");
  });

  it("an unknown centre yields an empty graph rather than throwing", () => {
    expect(neighbourhood(g, "sources/x.md", 1)).toEqual({ nodes: [], edges: [] });
  });
});

describe("oidForSourceSlug", () => {
  it("inverts a result source slug", () => {
    expect(oidForSourceSlug("result-wt-r1")).toBe("result:wt-r1");
  });

  it("inverts an artifact source slug, splitting on the LAST hyphen", () => {
    // the artifact id itself may contain hyphens, so only the version id
    // (the final segment) may be split off.
    expect(oidForSourceSlug("artifact-fig-1-v3")).toBe("artifact:fig-1@v3");
  });

  it("returns empty for a slug that isn't a source page at all", () => {
    expect(oidForSourceSlug("auth-gate")).toBe("");
  });
});

describe("fitLabel", () => {
  it("returns the whole title when it fits", () => {
    expect(fitLabel("Alpha", 100, 5)).toBe("Alpha");   // 20 chars of room
    expect(fitLabel("Alpha", 25, 5)).toBe("Alpha");    // exactly 5
  });

  it("ellipsises a title that does not fit, within the room given", () => {
    // 4 chars of room: 3 kept plus the ellipsis, never 5 glyphs in 4 slots.
    expect(fitLabel("Ivywrel correlation", 20, 5)).toBe("Ivy…");
    expect(fitLabel("Ivywrel correlation", 20, 5)).toHaveLength(4);
  });

  it("draws nothing rather than a bare ellipsis when there is no room", () => {
    expect(fitLabel("Alpha", 9, 5)).toBe("");
    expect(fitLabel("Alpha", 0, 5)).toBe("");
    expect(fitLabel("Alpha", -40, 5)).toBe("");
  });

  it("never returns more characters than the room allows, at any width", () => {
    for (let avail = 0; avail < 200; avail += 1) {
      expect(fitLabel("a very long concept page title", avail, 5).length)
        .toBeLessThanOrEqual(Math.max(0, Math.floor(avail / 5)));
    }
  });
});
