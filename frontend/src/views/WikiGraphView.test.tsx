import { describe, it, expect, vi, beforeEach } from "vitest";
import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { api } from "../api";
import * as graphLayout from "../components/graphLayout";

// AppShell (Mantine) needs these to exist in jsdom, same polyfills as
// WikiLintView.test.tsx.
beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as never;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as never);
});

const graph = {
  nodes: [
    { id: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept",
      status: "draft", trust: "unverified", in_degree: 0, out_degree: 1,
      orphan: false, cluster: 0 },
    { id: "concepts/b.md", slug: "b", title: "Beta", type: "Concept",
      status: "draft", trust: "human-reviewed", in_degree: 1, out_degree: 0,
      orphan: false, cluster: 0 },
  ],
  edges: [
    { id: "t:a->b:refines", src: "concepts/a.md", dst: "concepts/b.md",
      type: "refines", confidence: "high", source: "wt-r1",
      typed: true, materialized: false },
  ],
};

// Minimal fixtures for the browse view (WikiView), which the click-navigation
// test lands on after following a node link — same shapes as WikiView.test.tsx.
const summary = {
  counts: { Concept: 2 }, trust: { unverified: 1, "machine-confirmed": 0, "human-reviewed": 1 },
  pages: 2, pending: 0, quarantined: [] as string[], run: null, last_run: null,
  ingests_since_lint: 0, lint: { error: 0, warn: 0 }, index_md: "# Index",
  wiki_model: "claude-sonnet-5", wiki_enabled: true,
  wiki_merge: "propose" as const, merge_proposals: 0,
};
const rows = [
  { path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha", status: "draft",
    trust: "unverified", stale_after: "", tags: [] },
  { path: "concepts/b.md", slug: "b", type: "Concept", title: "Beta", status: "draft",
    trust: "human-reviewed", stale_after: "", tags: [] },
];
const alphaPage = {
  path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha", status: "draft",
  trust: "unverified", stale_after: "", tags: [],
  description: "d", aliases: [], human_notes: "", verified: [],
  body: "# Alpha\n", relations: [], backlinks: [], sources: [],
};

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </MantineProvider></QueryClientProvider>,
  );
}

describe("WikiGraphView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getWikiGraph").mockResolvedValue(graph as never);
  });

  it("renders the graph view at /wiki/graph, not a wiki page named 'graph'", async () => {
    // Regression guard for the /wiki/graph route: it must resolve to
    // WikiGraphView (which calls getWikiGraph), not fall through to the
    // /wiki/* catch-all (WikiView, which would call getWikiPage(id, "graph")
    // as if "graph" were a page slug).
    const spy = vi.spyOn(api, "getWikiPage");
    renderAt("/programs/p1/wiki/graph");
    await waitFor(() => expect(api.getWikiGraph).toHaveBeenCalledWith("p1"));
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders one element per node, labelled by title", async () => {
    renderAt("/programs/p1/wiki/graph");
    expect(await screen.findByTitle("Alpha")).toBeTruthy();
    expect(await screen.findByTitle("Beta")).toBeTruthy();
  });

  it("clicking a node navigates to its own wiki page by slug", async () => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    const getPage = vi.spyOn(api, "getWikiPage").mockResolvedValue(alphaPage as never);

    renderAt("/programs/p1/wiki/graph");
    const node = await screen.findByTitle("Alpha");
    fireEvent.click(node);
    // Alpha's slug is "a" (its id "concepts/a.md" is not the slug) — proves
    // the click target's own slug is used, not its graph node id.
    await waitFor(() => expect(getPage).toHaveBeenCalledWith("p1", "a"));
  });

  it("fits the svg viewBox around nodes even when force layout spreads them far outside a fixed canvas", async () => {
    // At the 50-200 node scale the binding spec targets (design doc 5.4),
    // forceLayout's charge/collide forces land nodes well outside any fixed
    // canvas. A viewBox fitted to the real bounding box is what keeps them
    // visible instead of clipped; simulate that spread directly rather than
    // relying on the real simulation to wander far enough by chance.
    const far = [
      { id: "concepts/a.md", data: { label: "Alpha", stage: "", kind: "", status: "draft" },
        position: { x: 5000, y: -3000 }, style: {} },
      { id: "concepts/b.md", data: { label: "Beta", stage: "", kind: "", status: "draft" },
        position: { x: -4000, y: 6000 }, style: {} },
    ];
    vi.spyOn(graphLayout, "forceLayout").mockReturnValue(far);

    renderAt("/programs/p1/wiki/graph");
    const svg = await screen.findByRole("img", { name: "concept graph" });
    await waitFor(() => expect(svg.getAttribute("viewBox")).toBeTruthy());

    const [minX, minY, w, h] = svg.getAttribute("viewBox")!.split(" ").map(Number);
    const maxX = minX + w;
    const maxY = minY + h;
    for (const p of far) {
      expect(p.position.x).toBeGreaterThanOrEqual(minX);
      expect(p.position.x).toBeLessThanOrEqual(maxX);
      expect(p.position.y).toBeGreaterThanOrEqual(minY);
      expect(p.position.y).toBeLessThanOrEqual(maxY);
    }
  });
});
