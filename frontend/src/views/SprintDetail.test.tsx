import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import SprintDetail, { ResultCitations } from "./SprintDetail";
import { api, type Sprint } from "../api";

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

function sprint(over: Partial<Sprint> = {}): Sprint {
  return {
    id: "sp1", status: "executing", title: "Train the model", summary: "",
    goals: "train", priority: 0, preemptible: true, resources_required: {},
    distributed: false, rationale: "", plan: ["do it"], program: "p1",
    results: [], threads: [], agent_running: true, started_at: 1_700_000_000,
    error: "", lease: null, model: "m", activity: null,
    votes: { up: 0, down: 0, mine: 0 }, artifacts_bound: [], artifacts_create: [],
    ...over,
  } as Sprint;
}

function renderSprintPage(s: Sprint) {
  vi.spyOn(api, "getSprint").mockResolvedValue(s);
  vi.spyOn(api, "getProgram").mockResolvedValue({
    id: "p1", title: "P1", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", wiki_model: "", chat_model: "",
    worker_model: "", wiki_enabled: false, wiki_merge: "auto", instructions: "",
    max_proposed: 0, activations: [], last_run: null,
  } as never);
  vi.spyOn(api, "me").mockResolvedValue({ user: null, required: false });
  vi.spyOn(api, "listUsers").mockResolvedValue([]);
  vi.spyOn(api, "getSprintFiles").mockResolvedValue([]);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <MemoryRouter initialEntries={[`/sprints/${s.id}`]}>
          <Routes><Route path="/sprints/:id" element={<SprintDetail />} /></Routes>
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}

describe("stop action", () => {
  it("offers Stop in the overflow menu for an executing sprint and calls the API after confirm", async () => {
    vi.spyOn(api, "stopSprint").mockResolvedValue(sprint({ status: "executing" }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderSprintPage(sprint({ status: "executing" }));

    await screen.findByText("Train the model");
    fireEvent.click(screen.getByRole("button", { name: "more actions" }));
    fireEvent.click(await screen.findByText("Stop…"));

    expect(window.confirm).toHaveBeenCalledWith(
      "Stop sp1? Its agent is ended and any job on its host is stopped. What it has produced so far is kept.");
    await waitFor(() => expect(api.stopSprint).toHaveBeenCalledWith("sp1"));
  });

  it("does not call the API when the confirm is dismissed", async () => {
    const stop = vi.spyOn(api, "stopSprint").mockResolvedValue(sprint({ status: "executing" }));
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderSprintPage(sprint({ status: "executing" }));

    await screen.findByText("Train the model");
    fireEvent.click(screen.getByRole("button", { name: "more actions" }));
    fireEvent.click(await screen.findByText("Stop…"));

    expect(window.confirm).toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it("offers Stop for a hibernated sprint too", async () => {
    renderSprintPage(sprint({ status: "hibernated", agent_running: false }));
    await screen.findByText("Train the model");
    fireEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(await screen.findByText("Stop…")).toBeTruthy();
  });
});
