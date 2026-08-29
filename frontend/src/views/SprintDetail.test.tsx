import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ResultCitations } from "./SprintDetail";
import { api } from "../api";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as never;
  vi.restoreAllMocks();
});

function renderCitations(programId: string, resultId: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <MemoryRouter>
          <ResultCitations programId={programId} resultId={resultId} />
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}

describe("ResultCitations", () => {
  it("renders a chip linking to the citing page's own address, not its bare slug", async () => {
    vi.spyOn(api, "getWikiCitations").mockResolvedValue([
      { path: "concepts/ivywrel-correlation.md", slug: "ivywrel-correlation",
        title: "Ivywrel correlation", type: "Concept" },
    ] as never);

    renderCitations("p1", "wt-r1");

    const link = await screen.findByRole("link", { name: "Ivywrel correlation" });
    // The wiki addresses a page by its bundle path minus ".md"
    // ("concepts/ivywrel-correlation"), never by the bare slug
    // ("ivywrel-correlation") — the exact bug this whole-phase review found.
    expect(link.getAttribute("href")).toBe("/programs/p1/wiki/concepts/ivywrel-correlation");
  });

  it("renders nothing when the result has no citations", async () => {
    vi.spyOn(api, "getWikiCitations").mockResolvedValue([]);

    renderCitations("p1", "wt-r2");

    await waitFor(() => expect(api.getWikiCitations).toHaveBeenCalled());
    // MantineProvider injects its own global <style> into the container, so
    // asserting on the container's full textContent would fail regardless of
    // what ResultCitations rendered — scope to what the component itself
    // would have produced.
    expect(screen.queryByText(/cited in the wiki/i)).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
