import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

// Mock the API so no network + no real graph is returned.
vi.mock("../api", () => ({
  api: { getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }) },
}));

import LineageCard from "./LineageCard";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <LineageCard programId="p1" />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("LineageCard empty-state gate", () => {
  it("shows the placeholder and does NOT mount the graph (no expand control) when there are no nodes", async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText(/No lineage yet/i)).toBeTruthy());
    // hasGraph is false -> the expand control (which gates React Flow) is absent,
    // so the lazy LineageGraph chunk is never referenced.
    expect(screen.queryByLabelText("Expand graph")).toBeNull();
  });
});
