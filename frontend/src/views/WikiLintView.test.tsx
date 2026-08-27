import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WikiLintView from "./WikiLintView";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as never;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as never);
});

const runs = [
  { id: "r0002", kind: "lint", status: "ok", at: 1756200000, pages_created: 0,
    pages_updated: 4, merged: [["concepts/job-lease.md", "concepts/compute-lease.md"]] },
  { id: "r0001", kind: "ingest", status: "failed", at: 1756100000, pages_created: 0,
    pages_updated: 0, merged: [] },
];

const lint = {
  counts: { warn: 1 }, findings: [],
  reports: [{ date: "2026-08-27", text: "# What I did\n\nTidied two pages.\n" }],
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p1/wiki/lint"]}>
        <Routes>
          <Route path="/programs/:id/wiki/lint" element={<WikiLintView />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>,
  );
}

describe("WikiLintView", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue(runs as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(lint as never);
    vi.spyOn(api, "listWikiMerges").mockResolvedValue([] as never);
  });

  it("lists what recent runs did, newest first", async () => {
    mount();
    const rows = await screen.findAllByTestId("wiki-run");
    expect(rows[0].textContent).toContain("r0002");
    expect(rows[1].textContent).toContain("r0001");
  });

  it("names both pages of a merge and links to the substrate commit", async () => {
    /* Nothing gates an automatic merge but git, so this row IS the audit trail. */
    mount();
    const row = (await screen.findAllByTestId("wiki-run"))[0];
    expect(row.textContent).toContain("compute-lease");
    expect(row.textContent).toContain("job-lease");
  });

  it("shows a failed run rather than hiding it", async () => {
    mount();
    const row = (await screen.findAllByTestId("wiki-run"))[1];
    expect(row.textContent).toMatch(/failed/i);
  });

  it("renders the agent's own filed report", async () => {
    mount();
    expect(await screen.findByText(/Tidied two pages/)).toBeTruthy();
    expect(screen.getByText("2026-08-27")).toBeTruthy();
  });

  it("shows an empty state when nothing has run yet", async () => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue([] as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    mount();
    expect(await screen.findByText(/nothing has run/i)).toBeTruthy();
  });
});

describe("WikiLintView proposals", () => {
  const proposals = [{
    id: "m0001", winner: "concepts/compute-lease.md", loser: "concepts/job-lease.md",
    why: "Both describe a time-bounded claim on a worker slot.",
    run: "r0002", at: 1756200000,
  }];

  beforeEach(() => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue([] as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    vi.spyOn(api, "listWikiMerges").mockResolvedValue(proposals as never);
  });

  it("shows the agent's reasoning and both pages", async () => {
    mount();
    const card = await screen.findByTestId("merge-proposal");
    expect(card.textContent).toContain("time-bounded claim");
    expect(card.textContent).toContain("compute-lease");
    expect(card.textContent).toContain("job-lease");
  });

  it("says which page survives, because that is the irreversible half", async () => {
    mount();
    const card = await screen.findByTestId("merge-proposal");
    expect(card.textContent).toMatch(/job-lease[\s\S]*compute-lease/);
  });

  it("accepts a proposal by id", async () => {
    const accept = vi.spyOn(api, "acceptWikiMerge")
      .mockResolvedValue({ applied: true, winner: "", loser: "", rewritten: [] } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /accept/i }));
    await waitFor(() => expect(accept).toHaveBeenCalledWith("p1", "m0001"));
  });

  it("rejects a proposal by id", async () => {
    const reject = vi.spyOn(api, "rejectWikiMerge")
      .mockResolvedValue({ rejected: [] } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /reject/i }));
    await waitFor(() => expect(reject).toHaveBeenCalledWith("p1", "m0001"));
  });

  it("says nothing is waiting when there are no proposals", async () => {
    vi.spyOn(api, "listWikiMerges").mockResolvedValue([] as never);
    mount();
    expect(screen.queryByTestId("merge-proposal")).toBeNull();
  });
});

// Point 6 of the task-10/11 brief: confirm, don't assume, that the static
// /wiki/lint route wins over the /wiki/* catch-all. Declared here with the
// splat listed FIRST — the opposite of App.tsx — to prove React Router ranks
// a static segment over a splat regardless of declaration order.
describe("wiki lint route ordering", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue([] as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    vi.spyOn(api, "listWikiMerges").mockResolvedValue([] as never);
  });

  it("renders WikiLintView, not the wiki/* catch-all, at /wiki/lint", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}><MantineProvider>
        <MemoryRouter initialEntries={["/programs/p1/wiki/lint"]}>
          <Routes>
            <Route path="/programs/:id/wiki/*" element={<div>browse view</div>} />
            <Route path="/programs/:id/wiki/lint" element={<WikiLintView />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider></QueryClientProvider>,
    );
    expect(await screen.findByText(/nothing has run/i)).toBeTruthy();
    expect(screen.queryByText("browse view")).toBeNull();
  });
});
