import { describe, it, expect, vi, beforeAll } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

// Mock the API so no network + no real graph is returned.
vi.mock("../api", () => ({
  api: { getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }) },
}));

// React Flow needs layout APIs jsdom lacks; the card's own controls are what is tested.
vi.mock("./LineageGraph", () => ({ default: () => <div>lineage graph</div> }));

import { api } from "../api";
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

describe("LineageCard auto-layout (P10)", () => {
  it("offers Auto-layout by name, and it forgets the dragged positions", async () => {
    vi.mocked(api.getGraph).mockResolvedValueOnce({
      nodes: [{ id: "p1-c1", kind: "sprint", label: "A", status: "done" }], edges: [],
    } as never);
    localStorage.setItem("lineage-pos:p1", JSON.stringify({ "p1-c1": { x: 900, y: 900 } }));
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Auto-layout" }));
    expect(localStorage.getItem("lineage-pos:p1")).toBeNull();
    expect(await screen.findByText("lineage graph")).toBeTruthy();   // remounted, not gone
  });
});
