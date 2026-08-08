import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

const ledger = vi.fn();
vi.mock("../api", () => ({
  api: {
    getLedger: () => ledger(),
    getUsage: () => Promise.resolve(null),
    setCapacity: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [] }),
  },
}));

import Ledger from "./Ledger";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter><Ledger /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("Compute page", () => {
  it("warns that agents are unbounded when no worker cap is set", async () => {
    ledger.mockResolvedValue({ capacity: { cpu: 16 }, used: { cpu: 2 }, available: { cpu: 14 }, leases: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no worker cap/i)).toBeTruthy());
  });

  it("does not warn once a worker cap exists", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: { cpu: 2, workers: 1 },
      available: { cpu: 14, workers: 0 }, leases: [],
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/capacity in use/i)).toBeTruthy());
    expect(screen.queryByText(/no worker cap/i)).toBeNull();
  });

  it("opens the edit modal", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: {}, available: {}, leases: [],
    });
    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /edit capacity/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /edit capacity/i }));
    await waitFor(() => expect(screen.getByLabelText("workers capacity")).toBeTruthy());
  });
});
