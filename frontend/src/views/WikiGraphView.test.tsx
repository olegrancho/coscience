import { describe, it, expect, vi, beforeEach } from "vitest";
import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { api } from "../api";
import * as wikiGraphSim from "../components/wikiGraphSim";

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

  it("clicking a node navigates to its own wiki page by path", async () => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    const getPage = vi.spyOn(api, "getWikiPage").mockResolvedValue(alphaPage as never);

    renderAt("/programs/p1/wiki/graph");
    const node = await screen.findByTitle("Alpha");
    fireEvent.click(node);
    // Alpha's node id is "concepts/a.md" and its bare slug is "a" — a page is
    // addressed by its bundle path minus ".md" ("concepts/a"), not by the
    // bare slug, so this proves the path form is what's navigated to.
    await waitFor(() => expect(getPage).toHaveBeenCalledWith("p1", "concepts/a"));
  });

  // The old "viewBox fits the spread" test is gone with the hand-rolled SVG.
  // Panning, zooming and fit-to-view are @xyflow/react's job now, not ours, and
  // testing a dependency's own viewport maths would be testing the wrong thing.
  // What remains ours is that every node in the graph is handed to the renderer
  // however far the simulation flings it — nothing of ours clips the set.
  it("renders every node regardless of how far the simulation spreads them", async () => {
    renderAt("/programs/p1/wiki/graph");
    expect(await screen.findByTitle("Alpha")).toBeTruthy();
    expect(await screen.findByTitle("Beta")).toBeTruthy();
    await waitFor(() =>
      expect(document.querySelectorAll(".react-flow__node").length).toBe(2));
  });

  it("reports how many nodes are visible, and a filter never rebuilds the simulation", async () => {
    // The live-simulation form of "filtering never re-layouts": rebuilding the
    // sim would reseed every node and throw away the arrangement (and any drag)
    // the reader is looking at. Node POSITIONS cannot be asserted here — jsdom
    // gives React Flow no real geometry — so this pins the invariant at its
    // source instead: the simulation is constructed once and survives the
    // toggle. wikiGraphSim.test.ts covers what the physics then does.
    const withUntypedEdge = {
      nodes: [
        ...graph.nodes,
        { id: "concepts/c.md", slug: "c", title: "Gamma", type: "Concept",
          status: "draft", trust: "unverified", in_degree: 1, out_degree: 0,
          orphan: false, cluster: 0 },
      ],
      edges: [
        ...graph.edges,
        { id: "t:b->c:body-link", src: "concepts/b.md", dst: "concepts/c.md",
          type: "", confidence: "", source: "", typed: false, materialized: false },
      ],
    };
    vi.spyOn(api, "getWikiGraph").mockResolvedValue(withUntypedEdge as never);
    const build = vi.spyOn(wikiGraphSim, "createWikiSim");

    renderAt("/programs/p1/wiki/graph");
    expect(await screen.findByText(/showing 3 of 3 nodes/)).toBeTruthy();
    const builtOnce = build.mock.calls.length;
    expect(builtOnce).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText("typed only"));

    // Hiding an edge must never hide the nodes it connected (design 5.3) ...
    await waitFor(() => expect(screen.getByText(/showing 3 of 3 nodes/)).toBeTruthy());
    // ... nor rebuild the simulation underneath the reader.
    expect(build.mock.calls.length).toBe(builtOnce);
  });

  it("the tension lens repaints without rebuilding the simulation", async () => {
    // Previously this compared a <circle>'s cx before and after. Under React
    // Flow there is no cx, so that assertion silently became null === null —
    // the same vacuous shape this file was already burned by once. Pinned at
    // the simulation instead, which is what "does not move" actually means.
    const build = vi.spyOn(wikiGraphSim, "createWikiSim");
    renderAt("/programs/p1/wiki/graph");
    await screen.findByTitle("Alpha");
    const builtOnce = build.mock.calls.length;
    expect(builtOnce).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText("tension"));
    await waitFor(() =>
      expect((screen.getByLabelText("tension") as HTMLInputElement).checked).toBe(true));
    expect(build.mock.calls.length).toBe(builtOnce);
  });
});

describe("WikiGraphView focus mode", () => {
  // A 5-node chain a-b-c-d-e. Focusing on "b" with hops=2 reaches a, b, c, d
  // (b's own hop-1 neighbours are a and c; hop-2 adds d via c) but never e —
  // a genuine strict subset, so "focused" and "unfocused" render different
  // node counts and the test can actually tell them apart.
  const chain = {
    nodes: ["a", "b", "c", "d", "e"].map((s, i) => ({
      id: `concepts/${s}.md`, slug: s, title: s.toUpperCase(), type: "Concept",
      status: "draft", trust: "unverified",
      in_degree: i === 0 ? 0 : 1, out_degree: i === 4 ? 0 : 1,
      orphan: false, cluster: 0,
    })),
    edges: [
      { id: "a->b", src: "concepts/a.md", dst: "concepts/b.md", type: "refines",
        confidence: "high", source: "s", typed: true, materialized: false },
      { id: "b->c", src: "concepts/b.md", dst: "concepts/c.md", type: "refines",
        confidence: "high", source: "s", typed: true, materialized: false },
      { id: "c->d", src: "concepts/c.md", dst: "concepts/d.md", type: "refines",
        confidence: "high", source: "s", typed: true, materialized: false },
      { id: "d->e", src: "concepts/d.md", dst: "concepts/e.md", type: "refines",
        confidence: "high", source: "s", typed: true, materialized: false },
    ],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getWikiGraph").mockResolvedValue(chain as never);
  });

  it("?focus=<path> reduces the rendered nodes to that node's neighbourhood", async () => {
    renderAt("/programs/p1/wiki/graph?focus=concepts/b");
    // a, b, c, d are within 2 hops of b; e is not — a strict subset of the
    // 5-node graph, so this genuinely distinguishes focused from unfocused.
    expect(await screen.findByText(/showing 4 of 4 nodes/)).toBeTruthy();
    expect(await screen.findByTitle("A")).toBeTruthy();
    expect(await screen.findByTitle("D")).toBeTruthy();
    expect(screen.queryByTitle("E")).toBeNull();
  });

  it("shows no 'show whole graph' link when nothing is focused", async () => {
    renderAt("/programs/p1/wiki/graph");
    await screen.findByText(/showing 5 of 5 nodes/);
    expect(screen.queryByRole("link", { name: /show whole graph/i })).toBeNull();
  });

  it("offers 'show whole graph' while focused, and clearing it restores the full graph", async () => {
    renderAt("/programs/p1/wiki/graph?focus=concepts/b");
    await screen.findByText(/showing 4 of 4 nodes/);
    const link = screen.getByRole("link", { name: /show whole graph/i });

    fireEvent.click(link);

    await waitFor(() => expect(screen.getByText(/showing 5 of 5 nodes/)).toBeTruthy());
    expect(screen.queryByRole("link", { name: /show whole graph/i })).toBeNull();
    expect(await screen.findByTitle("E")).toBeTruthy();
  });
});
