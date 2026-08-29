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

function show(slug: string, pageType?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WikiNeighbourhood programId="p1" slug={slug} pageType={pageType} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getWikiGraph").mockResolvedValue(graph as never);
});

describe("WikiNeighbourhood", () => {
  it("shows the current page and its neighbours", async () => {
    // "concepts/a" is the page ADDRESS (bundle path minus ".md"), which is
    // what the caller (WikiView) actually passes as `slug` — not the bare
    // filename stem "a". A flat "a" fixture would pass by coincidence even
    // if the centre match compared against the wrong field.
    show("concepts/a");
    expect(await screen.findByTitle("Alpha")).toBeTruthy();
    expect(await screen.findByTitle("Beta")).toBeTruthy();
  });

  it("tells the reader when the page is not in the graph at all, with no pageType given", async () => {
    // No `pageType` is passed here (the "caller didn't look" case), so the
    // generic fallback message is what should show — not the Source-specific
    // one (covered separately below) or the "no such page" one.
    show("sources/result-wt-r1");
    expect(await screen.findByText(/not in the concept graph/i)).toBeTruthy();
  });

  it("tells the reader plainly when no page exists at the address at all", async () => {
    show("nope", "");
    expect(await screen.findByText(/no page was found/i)).toBeTruthy();
  });

  it("reports a load failure distinctly, not as 'not in the concept graph'", async () => {
    vi.spyOn(api, "getWikiGraph").mockRejectedValue(new Error("boom"));
    show("concepts/a");
    expect(await screen.findByText(/could not load/i)).toBeTruthy();
    expect(screen.queryByText(/not in the concept graph/i)).toBeNull();
  });

  it("lists the pages citing a Source page below the explanation", async () => {
    vi.spyOn(api, "getWikiCitations").mockResolvedValue([
      { path: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept" },
    ] as never);
    // The address "sources/result-wt-r1" (not the bare slug "result-wt-r1")
    // is what a real caller passes; oidForSourceSlug must be fed only the
    // last path segment to resolve the right object id.
    show("sources/result-wt-r1", "Source");
    expect(await screen.findByText(/sources are excluded/i)).toBeTruthy();
    expect(await screen.findByRole("link", { name: "Alpha" })).toBeTruthy();
    expect(api.getWikiCitations).toHaveBeenCalledWith("p1", "result:wt-r1");
  });
});
