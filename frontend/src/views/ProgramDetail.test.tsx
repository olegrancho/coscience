import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { notifications } from "@mantine/notifications";
import ProgramDetail from "./ProgramDetail";
import { api, type SprintRef } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as any);
});

// The server-notes card sits on this page and reads both of these. Empty here:
// with no server and no note it draws nothing, which is the state most of these
// tests want to ignore.
function mockHostNotes(notes: Record<string, string> = {}, hosts: any[] = []) {
  vi.spyOn(api, "getHostNotes").mockResolvedValue({ notes, reports: [] });
  vi.spyOn(api, "getLedger").mockResolvedValue({
    capacity: {}, used: {}, available: {}, leases: [], paused: false, hosts,
  } as any);
}

function mockProgram(instructions: string) {
  vi.spyOn(api, "getProgram").mockResolvedValue({
    id: "p", title: "P", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", activations: [], last_run: null,
    instructions,
  } as any);
  vi.spyOn(api, "listGuidance").mockResolvedValue([]);
  vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
  vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
  vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 0, pages: 0 } as any);
  mockHostNotes();
}

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p"]}>
        <Routes><Route path="/programs/:id" element={<ProgramDetail />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
}

describe("general instructions", () => {
  it("shows the stored instructions", async () => {
    mockProgram("Cite a source for every claim.");
    renderAt();
    await waitFor(() => expect(screen.getByText("Cite a source for every claim.")).toBeTruthy());
  });

  it("edits and saves them", async () => {
    mockProgram("");
    const save = vi.spyOn(api, "setProgramInstructions").mockResolvedValue({} as any);
    renderAt();
    // Unset, the card still says so — otherwise nobody would know it exists.
    await waitFor(() => expect(screen.getByText(/works from the goals/i)).toBeTruthy());

    // By title, not text: the guidance card below has an "Add" button too.
    fireEvent.click(screen.getByTitle("Add general instructions"));
    const box = await screen.findByPlaceholderText(/cite a source/i);
    fireEvent.change(box, { target: { value: "Be terse." } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(save).toHaveBeenCalledWith("p", "Be terse."));
  });
});

describe("server notes", () => {
  it("shows a note for each server the program runs on", async () => {
    mockProgram("");
    mockHostNotes({ gpu1: "Use conda env torch2." }, [
      { name: "gpu1", ssh: "gpu1", placeable: true, programs: ["p"], run_root: "",
        capacity: {}, available: {}, gpus: [], removing: false, waiting_on: [] },
    ]);
    renderAt();
    expect(await screen.findByText("Use conda env torch2.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit notes on gpu1" })).toBeTruthy();
  });

  it("leaves the page alone when no server has a note", async () => {
    mockProgram("");
    renderAt();
    await screen.findByText(/works from the goals/i);        // the page is up
    expect(screen.queryByText(/server notes/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Server notes" })).toBeNull();   // nor in the nav
  });

  it("lists itself in the nav, after the science, once there is one", async () => {
    mockProgram("");
    mockHostNotes({ gpu1: "Use conda env torch2." }, [
      { name: "gpu1", ssh: "gpu1", placeable: true, programs: ["p"], run_root: "",
        capacity: {}, available: {}, gpus: [], removing: false, waiting_on: [] },
    ]);
    renderAt();
    const entry = await screen.findByText("Server notes");
    expect(entry).toBeTruthy();
    // Housekeeping sits below the science it is not part of: Lineage is the last
    // science section, and both housekeeping entries follow it.
    const nav = entry.closest("nav") ?? entry.parentElement!.parentElement!;
    const labels = [...nav.querySelectorAll("a, button")].map((e) => e.textContent);
    expect(labels.indexOf("Server notes")).toBeGreaterThan(labels.indexOf("Lineage"));
  });
});

describe("replan", () => {
  async function clickReplan(reply: Record<string, unknown>) {
    mockProgram("");
    vi.spyOn(api, "replan").mockResolvedValue(
      { program: "p", cycle: 3, submitted: [], ...reply } as any);
    const show = vi.spyOn(notifications, "show").mockImplementation(() => "" as any);
    renderAt();
    fireEvent.click(await screen.findByText("Replan now"));
    await waitFor(() => expect(show).toHaveBeenCalled());
    // Not .at(-1): tsconfig targets ES2020, and `npm run build` typechecks tests.
    return show.mock.calls[show.mock.calls.length - 1][0] as { color?: string; message?: string };
  }

  it("warns when the planner stood down instead of claiming it re-planned", async () => {
    // A backed-off beat never reached the reasoner. Reported like an idle one — teal,
    // "Re-planned — no new proposals" — the human is told their escape hatch worked
    // when nothing ran at all.
    const n = await clickReplan({ skipped: true, backoff: true });
    expect(n.color).toBe("yellow");
    expect(String(n.message)).not.toMatch(/Re-planned/i);
    expect(String(n.message)).toMatch(/failed/i);          // says what happened
    expect(String(n.message)).toMatch(/runs\.jsonl/);      // ...and where to look
  });

  it("names the global pause instead of blaming an exhausted budget", async () => {
    // Paused and throttled both stop the beat, but only a throttle clears itself at
    // the usage reset. Telling a paused human to "wait for the reset" sends them off
    // to wait for something that will never help — Resume is the only way out.
    const n = await clickReplan({ skipped: true, paused: true });
    expect(n.color).toBe("yellow");
    expect(String(n.message)).toMatch(/paused/i);
    expect(String(n.message)).toMatch(/resume/i);
    expect(String(n.message)).not.toMatch(/reset/i);
  });

  it("still reports a healthy quiet cycle as success", async () => {
    const n = await clickReplan({ skipped: false });
    expect(n.color).toBe("teal");
    expect(String(n.message)).toMatch(/Re-planned/i);
  });
});

describe("wiki link", () => {
  it("links to the wiki and badges the pending count", async () => {
    mockProgram("");
    vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 4, pages: 18 } as any);
    renderAt();
    const link = await screen.findByRole("link", { name: /open wiki/i });
    expect(link.getAttribute("href")).toBe("/programs/p/wiki");
    // Scoped to the link: the page renders plenty of other zeros and counts, so a
    // global getByText("4") would pass or fail for unrelated reasons.
    expect(link.textContent).toMatch(/4/);
  });

  it("shows no badge when nothing is pending", async () => {
    mockProgram("");
    vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 0, pages: 3 } as any);
    renderAt();
    const link = await screen.findByRole("link", { name: /open wiki/i });
    expect(link.textContent).not.toMatch(/\d/);
  });

  it("says what the wiki holds and when it last ran", async () => {
    mockProgram("");
    vi.spyOn(api, "getWikiSummary").mockResolvedValue({
      pending: 2, pages: 30, counts: { Concept: 24, Entity: 5, Synthesis: 1 },
      last_run: { id: "r1", kind: "ingest", status: "ok", at: Date.now() / 1000 - 3 * 3600 },
    } as any);
    renderAt();
    expect(await screen.findByText(/24 concepts/)).toBeTruthy();
    expect(screen.getByText(/1 synthesis$/)).toBeTruthy();
    expect(screen.getByText(/2 pending/)).toBeTruthy();
    expect(screen.getByText(/last ingest/)).toBeTruthy();
    expect(screen.getByText("3h ago")).toBeTruthy();
  });
});

describe("a transient fetch failure", () => {
  const PROG = {
    id: "p", title: "Embeddings program", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", activations: [], last_run: null, instructions: "",
  } as any;

  function mockSides() {
    vi.spyOn(api, "listGuidance").mockResolvedValue([]);
    vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
    vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
    mockHostNotes();
  }

  function renderWith(qc: QueryClient) {
    return render(
      <QueryClientProvider client={qc}><MantineProvider>
        <MemoryRouter initialEntries={["/programs/p"]}>
          <Routes><Route path="/programs/:id" element={<ProgramDetail />} /></Routes>
        </MemoryRouter>
      </MantineProvider></QueryClientProvider>);
  }

  it("keeps the loaded program on screen when a background poll fails", async () => {
    // Every query polls every 10s and on window focus, so a backend restart or a proxy
    // blip lands here routinely. It used to swap the whole page for "Program not found"
    // — and stay that way until a full reload.
    mockSides();
    const gp = vi.spyOn(api, "getProgram").mockResolvedValue(PROG);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWith(qc);
    await waitFor(() => expect(screen.getByText("Embeddings program")).toBeTruthy());
    gp.mockRejectedValue(new Error("502 Bad Gateway"));
    await act(async () => { await qc.refetchQueries({ queryKey: ["program", "p"] }); });
    expect(screen.queryByText(/Program not found/i)).toBeNull();
    expect(screen.getByText("Embeddings program")).toBeTruthy();
  });

  it("still reports a program that genuinely is not there", async () => {
    mockSides();
    vi.spyOn(api, "getProgram").mockRejectedValue(new Error("404 Not Found"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWith(qc);
    await waitFor(() => expect(screen.getByText(/Program not found/i)).toBeTruthy());
  });
});

describe("experiments list", () => {
  function row(over: Partial<SprintRef> = {}): SprintRef {
    return {
      id: "p-s1", status: "executing", goals: "g", title: "Do the assay",
      results: [], model: "m", last_status_at: Date.now() / 1000,
      votes: { up: 0, down: 0, mine: 0 }, escalation_level: "",
      ...over,
    };
  }

  function mockProgramWithSprints(sprints: SprintRef[]) {
    vi.spyOn(api, "getProgram").mockResolvedValue({
      id: "p", title: "P", status: "active", goals: "g", report: "", cycle: 0,
      sprints, pm_model: "", workdir: "", activations: [], last_run: null, instructions: "",
    } as any);
    vi.spyOn(api, "listGuidance").mockResolvedValue([]);
    vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
    vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
    vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 0, pages: 0 } as any);
    mockHostNotes();
  }

  it("offers escalated and hibernated in the status filter", async () => {
    mockProgramWithSprints([
      row({ id: "p-s1", status: "escalated" }),
      row({ id: "p-s2", status: "hibernated" }),
    ]);
    renderAt();
    expect(await screen.findByRole("option", { name: "escalated (1)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "hibernated (1)" })).toBeTruthy();
  });

  it("flags a sprint escalated to a human with a 'needs you' badge", async () => {
    mockProgramWithSprints([
      row({ id: "p-s1", title: "Needs a human", status: "escalated", escalation_level: "human" }),
      row({ id: "p-s2", title: "Fine for now", status: "escalated", escalation_level: "pm" }),
    ]);
    renderAt();
    await screen.findByText("Needs a human");
    expect(screen.getByText("needs you")).toBeTruthy();
    // Only the human-level row gets the badge — not the pm-level one.
    const pmRow = screen.getByText("Fine for now").closest("div");
    expect(pmRow?.parentElement?.textContent).not.toMatch(/needs you/);
  });
});

describe("a held experiment on the list", () => {
  const WHY = "Waiting on checkpoint recovery before the headline is written.";

  function progWith(sprints: SprintRef[]) {
    return {
      id: "p", title: "Embeddings program", status: "active", goals: "g", report: "",
      cycle: 0, sprints, pm_model: "", workdir: "", activations: [], last_run: null,
      instructions: "",
    } as never;
  }
  function row(over: Partial<SprintRef> = {}): SprintRef {
    return { id: "p-s1", status: "approved", goals: "g", title: "Revise the manuscript",
             results: [], model: "m", last_status_at: 1_700_000_000,
             votes: { up: 0, down: 0, mine: 0 }, escalation_level: "", ...over } as SprintRef;
  }
  function renderProg(sprints: SprintRef[]) {
    vi.spyOn(api, "getProgram").mockResolvedValue(progWith(sprints));
    vi.spyOn(api, "listGuidance").mockResolvedValue([]);
    vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as never);
    vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
    mockHostNotes();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}><MantineProvider>
        <MemoryRouter initialEntries={["/programs/p"]}>
          <Routes><Route path="/programs/:id" element={<ProgramDetail />} /></Routes>
        </MemoryRouter>
      </MantineProvider></QueryClientProvider>);
  }

  it("marks a held row, so the hold shows without opening the sprint", async () => {
    renderProg([row({ hold: { why: WHY, at: 1_700_000_500, by: "pm" } })]);
    expect(await screen.findByText("held")).toBeTruthy();
  });

  it("leaves an ordinary approved row unmarked", async () => {
    renderProg([row()]);
    await screen.findByText("Revise the manuscript");
    expect(screen.queryByText("held")).toBeNull();
  });
});

describe("guidance to the AI", () => {
  function renderProg() {
    vi.spyOn(api, "getProgram").mockResolvedValue({
      id: "p", title: "Embeddings program", status: "active", goals: "g", report: "",
      cycle: 0, sprints: [], pm_model: "", workdir: "", activations: [], last_run: null,
      instructions: "",
    } as never);
    vi.spyOn(api, "listGuidance").mockResolvedValue([]);
    vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as never);
    vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
    mockHostNotes();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}><MantineProvider>
        <MemoryRouter initialEntries={["/programs/p"]}>
          <Routes><Route path="/programs/:id" element={<ProgramDetail />} /></Routes>
        </MemoryRouter>
      </MantineProvider></QueryClientProvider>);
  }

  it("sends the note on Ctrl+Enter", async () => {
    const add = vi.spyOn(api, "addGuidance").mockResolvedValue({} as never);
    renderProg();
    const box = await screen.findByPlaceholderText(/Add a note for the AI/);
    fireEvent.change(box, { target: { value: "weigh the homology split" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(add).toHaveBeenCalledWith("p", "weigh the homology split"));
  });

  it("lets a bare Enter write a second line instead of sending", async () => {
    // It was a single-line input where Enter sent, so a note could only ever be one
    // line long — and half a thought went to the planner on a stray keystroke.
    const add = vi.spyOn(api, "addGuidance").mockResolvedValue({} as never);
    renderProg();
    const box = await screen.findByPlaceholderText(/Add a note for the AI/);
    fireEvent.change(box, { target: { value: "first line" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(add).not.toHaveBeenCalled();
  });

  it("says which key sends", async () => {
    renderProg();
    expect(await screen.findByPlaceholderText(/⌘↵ to send/)).toBeTruthy();
  });
});
