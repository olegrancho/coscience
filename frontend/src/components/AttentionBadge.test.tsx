import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

const getAttention = vi.fn();
vi.mock("../api", () => ({ api: { getAttention: () => getAttention() } }));

import AttentionBadge from "./AttentionBadge";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <MemoryRouter><AttentionBadge /></MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => getAttention.mockReset());

describe("AttentionBadge", () => {
  it("shows a count linking to the first escalated sprint", async () => {
    getAttention.mockResolvedValue({ escalated_to_human: [
      { sprint_id: "s1", program: "p1", title: "First", what: "stuck", at: 1_700_000_000 },
      { sprint_id: "s2", program: "p1", title: "Second", what: "stuck too", at: 1_700_000_001 },
    ] });
    renderIt();
    const link = await screen.findByRole("link", { name: /2 need you/ });
    expect(link.getAttribute("href")).toBe("/sprints/s1");
  });

  it("renders nothing when there is nothing to attend to", async () => {
    getAttention.mockResolvedValue({ escalated_to_human: [] });
    renderIt();
    await waitFor(() => expect(getAttention).toHaveBeenCalled());
    expect(screen.queryByText(/need you/)).toBeNull();
  });
});
