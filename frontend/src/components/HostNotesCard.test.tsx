import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", async () => {
  const real = await vi.importActual<typeof import("../api")>("../api");
  return {
    NoteChangedError: real.NoteChangedError,
    api: { getLedger: vi.fn(), getHostNotes: vi.fn(), setHostNote: vi.fn(), listPrograms: vi.fn() },
  };
});

import { api, NoteChangedError, type HostNotes, type NoteHost } from "../api";
import HostNotesCard, { notePreview, noteRows } from "./HostNotesCard";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const host = (name: string, allowed = true, label = ""): NoteHost => ({ name, label, allowed });
const LOCAL = host("local");
const GPU1 = host("gpu1");
const GPU2 = host("gpu2", false);          // not for p1

function notes(over: Partial<HostNotes> = {}, hosts: NoteHost[] = [LOCAL, GPU1]): HostNotes {
  return { notes: {}, reports: [], hosts, ...over };
}

function renderCard(data: HostNotes) {
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

const expand = async (label: string) =>
  fireEvent.click(await screen.findByRole("button", { name: `Expand notes on ${label}` }));

describe("noteRows", () => {
  it("puts this machine first and the rest by name", () => {
    const rows = noteRows(notes({ notes: { gpu2: "old" } }, [GPU2, GPU1, LOCAL]));
    expect(rows.map((r) => r.host)).toEqual(["local", "gpu1", "gpu2"]);
    expect(rows[0].label).toBe("this machine");
  });

  it("calls a server by its display name when someone set one, this machine included", () => {
    expect(noteRows(notes({}, [host("local", true, "avatar")]))[0].label).toBe("avatar");
    expect(noteRows(notes({}, [host("gpu1", true, "the big one")]))[0].label).toBe("the big one");
  });

  it("leaves out a server that neither takes this program's work nor holds anything", () => {
    expect(noteRows(notes({}, [GPU2])).map((r) => r.host)).toEqual([]);
  });

  it("marks a server with a note but no access, or no longer in the pool, as stale", () => {
    const rows = noteRows(notes({ notes: { gpu2: "x", gone: "y" } }, [GPU2]));
    expect(rows.map((r) => [r.host, r.stale])).toEqual([["gone", true], ["gpu2", true]]);
  });
});

describe("notePreview", () => {
  it("is the first line with text, without its markdown lead", () => {
    expect(notePreview("\n## Environments\nconda env t23")).toBe("Environments");
    expect(notePreview("- torch 2.3 in t23\n- CUDA 11")).toBe("torch 2.3 in t23");
  });

  it("is cut short when long", () => {
    expect(notePreview("x".repeat(200), 10)).toBe(`${"x".repeat(9)}…`);
  });
});

describe("HostNotesCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not read the ledger — the servers come with the notes (O23)", async () => {
    renderCard(notes({ notes: { gpu1: "Use conda env torch2." } }));
    await screen.findByText("gpu1");
    expect(api.getLedger).not.toHaveBeenCalled();
  });

  it("starts collapsed, each row saying enough to tell whether to open it (P12)", async () => {
    renderCard(notes({
      notes: { gpu1: "Use conda env torch2.\n\nLong detail nobody needs at a glance." },
      reports: [{ id: "r1", sprint_id: "p1-s3", host: "gpu1", text: "CUDA 11 only", source: "finished", at: 1 }],
    }));
    expect(await screen.findByText("Use conda env torch2.")).toBeTruthy();   // the preview
    expect(screen.queryByText(/Long detail/)).toBeNull();
    expect(screen.getByText("1 report unread")).toBeTruthy();
    expect(screen.getByText("no notes yet")).toBeTruthy();                    // this machine
    expect(screen.queryByText(/CUDA 11 only/)).toBeNull();
  });

  it("opens a row to show the whole note and its reports, and closes it again", async () => {
    renderCard(notes({
      notes: { gpu1: "Use conda env torch2.\n\nLong detail." },
      reports: [
        { id: "r1", sprint_id: "p1-s3", host: "gpu1", text: "CUDA 11 only", source: "finished", at: 1 },
        { id: "r2", sprint_id: "p1-s4", host: "gpu1", text: "/scratch was full", source: "escalation", at: 2 },
      ],
    }));
    await expand("gpu1");
    expect(screen.getByText("Long detail.")).toBeTruthy();
    expect(screen.getByText("from p1-s3 (finished): CUDA 11 only")).toBeTruthy();
    expect(screen.getByText("from p1-s4 (escalation): /scratch was full")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Collapse notes on gpu1" }));
    expect(screen.queryByText("Long detail.")).toBeNull();
  });

  it("edits a note and saves the typed text against the note as opened", async () => {
    vi.mocked(api.setHostNote).mockResolvedValue(notes({ notes: { gpu1: "torch 2.3 in env t23" } }));
    renderCard(notes({ notes: { gpu1: "old note" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    const box = screen.getByRole("textbox");
    expect((box as HTMLTextAreaElement).value).toBe("old note");   // seeded with the note
    fireEvent.change(box, { target: { value: "torch 2.3 in env t23" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(api.setHostNote)
      .toHaveBeenCalledWith("p1", "gpu1", "torch 2.3 in env t23", [], "old note"));
    // The saved note is on screen from the response, with no second fetch.
    expect(await screen.findByText("torch 2.3 in env t23")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(api.getHostNotes).toHaveBeenCalledTimes(1);
  });

  it("says so on the row when the save fails, and keeps what was typed", async () => {
    vi.mocked(api.setHostNote).mockRejectedValue(new Error("500 disk full"));
    renderCard(notes({ notes: { gpu1: "old note" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "new note" } });
    fireEvent.click(screen.getByText("Save"));

    expect(await screen.findByText(/500 disk full/)).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("new note");
  });

  it("shows their version when someone saved first, and only overwrites it on a second, deliberate save (O22)", async () => {
    vi.mocked(api.setHostNote)
      .mockRejectedValueOnce(new NoteChangedError("what the other person wrote"))
      .mockResolvedValueOnce(notes({ notes: { gpu1: "mine" } }));
    renderCard(notes({ notes: { gpu1: "as I opened it" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "mine" } });
    fireEvent.click(screen.getByText("Save"));

    expect(await screen.findByText(/changed this note since you opened it/)).toBeTruthy();
    expect(screen.getByText("what the other person wrote")).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("mine");

    fireEvent.click(screen.getByText("Save mine over theirs"));
    await waitFor(() => expect(api.setHostNote).toHaveBeenLastCalledWith(
      "p1", "gpu1", "mine", [], "what the other person wrote"));
    expect(await screen.findByText("mine")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("discards the edit on Cancel", async () => {
    renderCard(notes({ notes: { gpu1: "old note" } }));

    fireEvent.click(await screen.findByRole("button", { name: "Edit notes on gpu1" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "throw this away" } });
    fireEvent.click(screen.getByText("Cancel"));

    expect(api.setHostNote).not.toHaveBeenCalled();
    expect(await screen.findByText("old note")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("keeps showing a server that has a note but no longer takes this program's work", async () => {
    renderCard(notes({ notes: { gpu2: "kept from when it ran p1" } }, [GPU1, GPU2]));
    expect(await screen.findByText("gpu2")).toBeTruthy();
    expect(screen.getAllByText(/no longer used by this program/)).toHaveLength(1);
    expect(screen.getByText("kept from when it ran p1")).toBeTruthy();
  });

  it("offers to mark a withdrawn server's reports read, since the planner no longer will (O22)", async () => {
    const report = { id: "r1", sprint_id: "p1-s3", host: "gpu2", text: "old news",
                     source: "finished" as const, at: 1 };
    vi.mocked(api.setHostNote).mockResolvedValue(notes({ notes: { gpu2: "kept" } }, [GPU2]));
    renderCard(notes({ notes: { gpu2: "kept" }, reports: [report] }, [GPU2]));
    await expand("gpu2");
    expect(screen.getByText(/planner will not fold these in/)).toBeTruthy();
    fireEvent.click(screen.getByText("Mark read"));
    await waitFor(() => expect(api.setHostNote)
      .toHaveBeenCalledWith("p1", "gpu2", "kept", ["r1"], "kept"));
  });

  it("does not offer Mark read on a server the planner still looks after", async () => {
    renderCard(notes({ reports: [{ id: "r1", sprint_id: "p1-s3", host: "gpu1", text: "x",
                                   source: "finished", at: 1 }] }));
    await expand("gpu1");
    expect(screen.queryByText("Mark read")).toBeNull();
  });

  it("renders nothing when no server has anything to say", async () => {
    renderCard(notes({}, []));
    await waitFor(() => expect(api.getHostNotes).toHaveBeenCalled());
    expect(screen.queryByText(/server notes/)).toBeNull();
  });

  it("draws nothing until the notes have arrived", async () => {
    // Editing seeds the textarea from the notes query, so a row drawn before it
    // answers would offer an empty draft that Save writes over the real note.
    vi.mocked(api.getHostNotes).mockReturnValue(new Promise(() => {}));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <HostNotesCard programId="p1" />
        </QueryClientProvider>
      </MantineProvider>,
    );
    await waitFor(() => expect(api.getHostNotes).toHaveBeenCalled());
    expect(screen.queryByText(/server notes/)).toBeNull();
  });

  it("asks to clear only the reports it actually showed", async () => {
    // A sprint can file a report between this page loading and Save landing; that one
    // has been read by nobody, so the save must not sweep it away.
    vi.mocked(api.setHostNote).mockResolvedValue(notes({ notes: { gpu1: "folded" } }));
    renderCard(notes({
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
      "p1", "gpu1", "folded", ["p1-s3:finished:1", "p1-s4:escalation:2"], "old note"));
  });
});
