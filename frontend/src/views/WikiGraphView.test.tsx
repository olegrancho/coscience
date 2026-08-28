import { describe, it, expect, vi, beforeEach } from "vitest";
import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { api } from "../api";

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

  it("routes /wiki/graph to the graph view, not to a page named 'graph'", async () => {
    // The defect this catches: registering /wiki/graph AFTER /wiki/*, which
    // makes WikiView render a wiki page whose slug is "graph".
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
});
