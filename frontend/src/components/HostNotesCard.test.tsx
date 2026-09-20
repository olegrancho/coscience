import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: { getLedger: vi.fn(), getHostNotes: vi.fn(), setHostNote: vi.fn(), listPrograms: vi.fn() },
}));

import { api, type HostNotes, type LedgerHost } from "../api";
import HostNotesCard, { noteRows } from "./HostNotesCard";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function host(over: Partial<LedgerHost> & { name: string }): LedgerHost {
  return {
    ssh: "", placeable: true, programs: null, run_root: "",
    capacity: {}, available: {}, gpus: [], removing: false, waiting_on: [],
    ...over,
  };
}

const LOCAL = host({ name: "local" });                            // programs null: admits p1
const GPU1 = host({ name: "gpu1", ssh: "gpu1", programs: ["p1"] });
const GPU2 = host({ name: "gpu2", ssh: "gpu2", programs: ["other"] });   // not for p1

function notes(over: Partial<HostNotes> = {}): HostNotes {
  return { notes: {}, reports: [], ...over };
}

function renderCard(hosts: LedgerHost[], data: HostNotes) {
  vi.mocked(api.getLedger).mockResolvedValue({
    capacity: {}, used: {}, available: {}, leases: [], paused: false, hosts,
  });
  vi.mocked(api.getHostNotes).mockResolvedValue(data);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <HostNotesCard programId="p1" />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("noteRows", () => {
  it("puts this machine first and the rest by name", () => {
    const rows = noteRows([GPU2, GPU1, LOCAL], "p1", notes({ notes: { gpu2: "old" } }));
    expect(rows.map((r) => r.host)).toEqual(["local", "gpu1", "gpu2"]);
    expect(rows[0].label).toBe("this machine");
  });

  it("calls this machine by its display name once someone sets one", () => {
    const named = { ...LOCAL, label: "avatar" };
    expect(noteRows([named], "p1", { notes: {}, reports: [] })[0].label).toBe("avatar");
    // Only until then does it read as "this machine".
    expect(noteRows([LOCAL], "p1", { notes: {}, reports: [] })[0].label).toBe("this machine");
  });

  it("calls a server by its display name when someone set one", () => {
    const rows = noteRows([host({ name: "gpu1", ssh: "gpu1", label: "the big one" })], "p1", notes());
    expect(rows[0].label).toBe("the big one");
  });

  it("leaves out a server that neither takes this program's work nor holds anything", () => {
    expect(noteRows([GPU2], "p1", notes()).map((r) => r.host)).toEqual([]);
  });
});

describe("HostNotesCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists the servers this program runs on, with their notes", async () => {
    renderCard([LOCAL, GPU1], notes({ notes: { gpu1: "Use conda env torch2." } }));
    expect(await screen.findByText("this machine")).toBeTruthy();
    expect(screen.getByText("gpu1")).toBeTruthy();
    expect(screen.getByText("Use conda env torch2.")).toBeTruthy();
    // The machine with no note says so rather than looking broken.
    expect(screen.getByText("No notes yet.")).toBeTruthy();
  });

  it("shows what finished and escalated sprints reported about a server", async () => {
    renderCard([GPU1], notes({
      reports: [
        { id: "r1", sprint_id: "p1-s3", host: "gpu1", text: "CUDA 11 only", source: "finished", at: 1 },
        { id: "r2", sprint_id: "p1-s4", host: "gpu1", text: "/scratch was full", source: "escalation", at: 2 },
      ],
    }));
    expect(await screen.findByText("from p1-s3 (finished): CUDA 11 only")).toBeTruthy();
    expect(screen.getByText("from p1-s4 (escalation): /scratch was full")).toBeTruthy();
  });

  it("edits a note and saves the typed text", async () => {
    vi.mocked(api.setHostNote).mockResolvedValue(
      notes({ notes: { gpu1: "torch 2.3 in env t23" } }));
    renderCard([GPU1], notes({ notes: { gpu1: "old note" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    const box = screen.getByRole("textbox");
    expect((box as HTMLTextAreaElement).value).toBe("old note");   // seeded with the note
    fireEvent.change(box, { target: { value: "torch 2.3 in env t23" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(api.setHostNote)
      .toHaveBeenCalledWith("p1", "gpu1", "torch 2.3 in env t23", []));
    // The saved note is on screen from the response, with no second fetch.
    expect(await screen.findByText("torch 2.3 in env t23")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(api.getHostNotes).toHaveBeenCalledTimes(1);
  });

  it("says so on the row when the save fails, and keeps what was typed", async () => {
    vi.mocked(api.setHostNote).mockRejectedValue(new Error("500 disk full"));
    renderCard([GPU1], notes({ notes: { gpu1: "old note" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "new note" } });
    fireEvent.click(screen.getByText("Save"));

    expect(await screen.findByText(/500 disk full/)).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("new note");
  });

  it("discards the edit on Cancel", async () => {
    renderCard([GPU1], notes({ notes: { gpu1: "old note" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "throw this away" } });
    fireEvent.click(screen.getByText("Cancel"));

    expect(api.setHostNote).not.toHaveBeenCalled();
    expect(await screen.findByText("old note")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("keeps showing a server that has a note but no longer takes this program's work", async () => {
    renderCard([GPU1, GPU2], notes({ notes: { gpu2: "kept from when it ran p1" } }));
    expect(await screen.findByText("gpu2")).toBeTruthy();
    expect(screen.getByText(/no longer used by this program/)).toBeTruthy();
    expect(screen.getByText("kept from when it ran p1")).toBeTruthy();
    // The server that does run the program is not labelled that way.
    expect(screen.getAllByText(/no longer used by this program/)).toHaveLength(1);
  });

  it("renders nothing when no server has anything to say", async () => {
    renderCard([], notes());
    await waitFor(() => expect(api.getHostNotes).toHaveBeenCalled());
    expect(screen.queryByText(/server notes/)).toBeNull();
  });

  it("draws nothing until both the notes and the servers have arrived", async () => {
    // Editing seeds the textarea from the notes query, so a row drawn before it
    // answers would offer an empty draft that Save writes over the real note —
    // and every server would be wrongly marked as no longer used.
    vi.mocked(api.getLedger).mockResolvedValue({
      capacity: {}, used: {}, available: {}, leases: [], paused: false, hosts: [GPU1],
    });
    vi.mocked(api.getHostNotes).mockReturnValue(new Promise(() => {}));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <HostNotesCard programId="p1" />
        </QueryClientProvider>
      </MantineProvider>,
    );
    await waitFor(() => expect(api.getLedger).toHaveBeenCalled());
    expect(screen.queryByText(/server notes/)).toBeNull();
    expect(screen.queryByText("gpu1")).toBeNull();
  });

  it("asks to clear only the reports it actually showed", async () => {
    // A sprint can file a report between this page loading and Save landing; that one
    // has been read by nobody, so the save must not sweep it away.
    vi.mocked(api.setHostNote).mockResolvedValue(notes({ notes: { gpu1: "folded" } }));
    renderCard([GPU1], notes({
      notes: { gpu1: "old note" },
      reports: [
        { id: "p1-s3:finished:1", sprint_id: "p1-s3", host: "gpu1", text: "CUDA 11 only",
          source: "finished", at: 1 },
        { id: "p1-s4:escalation:2", sprint_id: "p1-s4", host: "gpu1", text: "/scratch was full",
          source: "escalation", at: 2 },
      ],
    }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "folded" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(api.setHostNote).toHaveBeenCalledWith(
      "p1", "gpu1", "folded", ["p1-s3:finished:1", "p1-s4:escalation:2"]));
  });
});
