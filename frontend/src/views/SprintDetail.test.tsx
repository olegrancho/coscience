import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import SprintDetail, { ResultCitations } from "./SprintDetail";
import { notifications } from "@mantine/notifications";
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

describe("artifacts card", () => {
  const fig = { aid: "kernel-shape", title: "Kernel shape", kind: "figure" };

  it("links a promised artifact once it exists, naming its version", async () => {
    renderSprintPage(sprint({ status: "done", agent_running: false,
                              artifacts_create: [{ ...fig, exists: true, version: "v1" }] }));

    const link = await screen.findByRole("link", { name: "Kernel shape" });
    expect(link.getAttribute("href")).toBe("/programs/p1/artifacts/kernel-shape");
    expect(screen.getByText("v1 —")).toBeTruthy();
    // The promise is gone: a sprint that made the thing must not still advertise it.
    expect(screen.queryByText(/will be created/)).toBeNull();
  });

  it("still reads as a promise, with no link, when the artifact was never made", async () => {
    renderSprintPage(sprint({ status: "done", agent_running: false,
                              artifacts_create: [{ ...fig, exists: false, version: "" }] }));

    await screen.findByText("Train the model");
    expect(screen.getByText("will be created —")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Kernel shape" })).toBeNull();
  });

  it("links an artifact that exists but has no version cut yet", async () => {
    renderSprintPage(sprint({ artifacts_create: [{ ...fig, exists: true, version: "" }] }));

    const link = await screen.findByRole("link", { name: "Kernel shape" });
    expect(link.getAttribute("href")).toBe("/programs/p1/artifacts/kernel-shape");
    expect(screen.getByText("no version yet —")).toBeTruthy();
  });

  it("treats a spec from a backend that says nothing about existence as a promise", async () => {
    renderSprintPage(sprint({ artifacts_create: [fig] }));

    await screen.findByText("Train the model");
    expect(screen.getByText("will be created —")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Kernel shape" })).toBeNull();
  });

  it("keeps bound artifacts linked by id alongside the created ones", async () => {
    renderSprintPage(sprint({ artifacts_bound: ["manuscript"],
                              artifacts_create: [{ ...fig, exists: true, version: "v2" }] }));

    expect((await screen.findByRole("link", { name: "manuscript" })).getAttribute("href"))
      .toBe("/programs/p1/artifacts/manuscript");
    expect(screen.getByRole("link", { name: "Kernel shape" })).toBeTruthy();
  });
});

describe("restore a canceled sprint", () => {
  const spyShow = () =>
    vi.spyOn(notifications, "show").mockImplementation(() => "" as never);

  it("offers Restore and says which status it came back as", async () => {
    const show = spyShow();
    vi.spyOn(api, "restoreSprint").mockResolvedValue(
      sprint({ status: "proposed", agent_running: false }));
    renderSprintPage(sprint({ status: "canceled", agent_running: false }));

    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(api.restoreSprint).toHaveBeenCalledWith("sp1"));
    // Where it lands depends on how it was canceled, so the message must come from
    // the status the backend returned, never from a guess made here.
    await waitFor(() => expect(show).toHaveBeenCalledWith(expect.objectContaining({
      title: "Restored",
      message: expect.stringContaining("Back in proposed, where it was canceled from"),
    })));
  });

  it("says a mid-run cancel comes back as a fresh run, not just 'queued'", async () => {
    const show = spyShow();
    vi.spyOn(api, "restoreSprint").mockResolvedValue(
      sprint({ status: "queued", agent_running: false }));
    renderSprintPage(sprint({ status: "canceled", agent_running: false }));

    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(show).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining("fresh run"),
    })));
  });

  it("surfaces a refusal instead of pretending it worked", async () => {
    // A demoted sprint cannot come back: its life continued as an idea.
    const show = spyShow();
    vi.spyOn(api, "restoreSprint").mockRejectedValue(new Error("was demoted to an idea"));
    renderSprintPage(sprint({ status: "canceled", agent_running: false }));

    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(show).toHaveBeenCalledWith(expect.objectContaining({
      color: "red", title: "Couldn't restore",
      message: expect.stringContaining("demoted to an idea"),
    })));
  });

  it("offers no Restore on a sprint that was never canceled", async () => {
    renderSprintPage(sprint({ status: "done", agent_running: false }));
    await screen.findByText("Train the model");
    expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
  });
});
