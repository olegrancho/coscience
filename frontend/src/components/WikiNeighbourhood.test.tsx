import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WikiNeighbourhood from "./WikiNeighbourhood";
import { api } from "../api";

const graph = {
  nodes: [
    { id: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 0, out_degree: 1, orphan: false, cluster: 0 },
    { id: "concepts/b.md", slug: "b", title: "Beta", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 1, out_degree: 0, orphan: false, cluster: 0 },
  ],
  edges: [{ id: "e", src: "concepts/a.md", dst: "concepts/b.md", type: "refines",
            confidence: "high", source: "s", typed: true, materialized: false }],
};

function show(slug: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WikiNeighbourhood programId="p1" slug={slug} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getWikiGraph").mockResolvedValue(graph as never);
});

describe("WikiNeighbourhood", () => {
  it("shows the current page and its neighbours", async () => {
    show("a");
    expect(await screen.findByTitle("Alpha")).toBeTruthy();
    expect(await screen.findByTitle("Beta")).toBeTruthy();
  });

  it("tells the reader when the page is not in the graph at all", async () => {
    // Source pages are excluded from the graph by design; an empty box reads as
    // broken, so the pane must say so instead.
    show("result-wt-r1");
    expect(await screen.findByText(/not in the concept graph/i)).toBeTruthy();
  });
});
