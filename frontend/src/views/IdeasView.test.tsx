import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import IdeasView from "./IdeasView";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  vi.spyOn(api, "getProgram").mockResolvedValue({
    id: "p", title: "P", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", activations: [], last_run: null, instructions: "",
  } as any);
  vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
  vi.spyOn(api, "listGuidance").mockResolvedValue([]);
  vi.spyOn(api, "listUsers").mockResolvedValue([] as any);
});

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p/ideas"]}>
        <Routes><Route path="/programs/:id/ideas" element={<IdeasView />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
}

describe("compress / brainstorm", () => {
  async function clickCompress(reply: Record<string, unknown>) {
    vi.spyOn(api, "pmDirective").mockResolvedValue(
      { program: "p", cycle: 3, submitted: [], ...reply } as any);
    const show = vi.spyOn(notifications, "show").mockImplementation(() => "" as any);
    renderAt();
    fireEvent.click(await screen.findByText("Compress"));
    await waitFor(() => expect(show).toHaveBeenCalled());
    // Not .at(-1): tsconfig targets ES2020, and `npm run build` typechecks tests.
    return show.mock.calls[show.mock.calls.length - 1][0] as { color?: string; message?: string };
  }

  it("says the planner stood down rather than 'nothing to do'", async () => {
    // These buttons force a beat, so they are the same escape hatch as Replan — and
    // a backed-off beat never ran. "Nothing to do this cycle" would describe a
    // healthy pool, not a planner that has given up.
    const n = await clickCompress({ skipped: true, backoff: true });
    expect(String(n.message)).not.toMatch(/Nothing to do/i);
    expect(String(n.message)).toMatch(/failed/i);
    expect(String(n.message)).toMatch(/runs\.jsonl/);
  });

  it("still reports an ordinary quiet cycle as before", async () => {
    const n = await clickCompress({ skipped: true });
    expect(String(n.message)).toMatch(/Nothing to do/i);
  });
});
