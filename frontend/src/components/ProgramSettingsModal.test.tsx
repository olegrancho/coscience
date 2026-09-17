import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    setProgramModel: vi.fn().mockResolvedValue({}),
    setProgramWikiModel: vi.fn().mockResolvedValue({}),
    setProgramChatModel: vi.fn().mockResolvedValue({}),
    setProgramWorkerModel: vi.fn().mockResolvedValue({}),
    setProgramWikiEnabled: vi.fn().mockResolvedValue({}),
    setWikiMergePolicy: vi.fn().mockResolvedValue({ id: "p1", wiki_merge: "propose" }),
    setProgramWorkdir: vi.fn().mockResolvedValue({ id: "p1", workdir: "/tmp/proj2", exists: true }),
    setProgramMaxProposed: vi.fn().mockResolvedValue({}),
    setProgramInstructions: vi.fn().mockResolvedValue({}),
    listDirs: vi.fn().mockResolvedValue({ path: null, parent: null, roots: [], entries: [] }),
    getLedger: vi.fn().mockResolvedValue({
      capacity: {}, used: {}, available: {}, leases: [], paused: false,
      hosts: [
        { name: "local", ssh: "", placeable: true, programs: [], exclude_programs: [], run_root: "",
          capacity: {}, available: {}, gpus: [] },
        { name: "gpu1", ssh: "gpu1", placeable: true, programs: ["p1"], exclude_programs: [], run_root: "~/runs",
          capacity: {}, available: {}, gpus: [] },
        { name: "gpu2", ssh: "gpu2", placeable: true, programs: [], exclude_programs: ["p1"], run_root: "~/runs",
          capacity: {}, available: {}, gpus: [] },
      ],
    }),
    setProgramHosts: vi.fn().mockResolvedValue({
      capacity: {}, used: {}, available: {}, leases: [], paused: false, hosts: [], cut_off: [],
    }),
  },
}));

vi.mock("@mantine/notifications", () => ({
  notifications: { show: vi.fn() },
}));

import { api } from "../api";
import { notifications } from "@mantine/notifications";
import ProgramSettingsModal from "./ProgramSettingsModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
  // Mantine's ScrollArea.Autosize (inside the DirectoryPickerModal this component
  // renders) uses ResizeObserver, absent in jsdom (see DirectoryPickerModal.test.tsx).
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as unknown as typeof ResizeObserver);
});

const program = {
  id: "p1", title: "A", status: "active", goals: "x",
  report: "", cycle: 0, sprints: [], pm_model: "claude-opus-5",
  wiki_model: "claude-sonnet-5", chat_model: "claude-fable-5-1", worker_model: "claude-sonnet-5",
  wiki_enabled: true, wiki_merge: "auto",
  workdir: "/tmp/proj", instructions: "be careful", max_proposed: 6,
  activations: [], last_run: null,
};

function renderModal(overrides = {}) {
  const onSaved = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <ProgramSettingsModal opened onClose={() => {}} onSaved={onSaved}
          program={{ ...program, ...overrides } as never} />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return { ...view, onSaved };
}

describe("ProgramSettingsModal", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("seeds every field from the program", () => {
    renderModal();
    expect((screen.getByLabelText("planner model") as HTMLSelectElement).value).toBe("claude-opus-5");
    expect((screen.getByLabelText("project folder") as HTMLInputElement).value).toBe("/tmp/proj");
    expect((screen.getByLabelText("max proposed experiments") as HTMLInputElement).value).toBe("6");
    expect((screen.getByLabelText("standing instructions") as HTMLTextAreaElement).value).toBe("be careful");
  });

  it("shows an unset cap as blank", () => {
    renderModal({ max_proposed: 0 });
    expect((screen.getByLabelText("max proposed experiments") as HTMLInputElement).value).toBe("");
  });

  it("posts only the field that changed", async () => {
    const { onSaved } = renderModal();
    fireEvent.change(screen.getByLabelText("max proposed experiments"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.setProgramMaxProposed).toHaveBeenCalledWith("p1", 3));
    expect(api.setProgramWorkdir).not.toHaveBeenCalled();
    expect(api.setProgramInstructions).not.toHaveBeenCalled();
    expect(api.setProgramModel).not.toHaveBeenCalled();
    expect(api.setProgramWikiModel).not.toHaveBeenCalled();
    expect(api.setProgramWikiEnabled).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalled();
  });

  it("seeds the wiki model separately from the planner model", () => {
    renderModal();
    expect((screen.getByLabelText("planner model") as HTMLSelectElement).value)
      .toBe("claude-opus-5");
    expect((screen.getByLabelText("wiki model") as HTMLSelectElement).value)
      .toBe("claude-sonnet-5");
  });

  it("posts the wiki model without touching the planner model", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("wiki model"),
                     { target: { value: "claude-opus-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.setProgramWikiModel).toHaveBeenCalledWith("p1", "claude-opus-5"));
    expect(api.setProgramModel).not.toHaveBeenCalled();
  });

  it("posts the chat model without touching the planner model", async () => {
    // H5: chat used to borrow the planner's model, so tuning one moved the other.
    renderModal();
    expect((screen.getByLabelText("chat model") as HTMLSelectElement).value).toBe("claude-fable-5-1");
    fireEvent.change(screen.getByLabelText("chat model"), { target: { value: "claude-opus-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.setProgramChatModel).toHaveBeenCalledWith("p1", "claude-opus-5"));
    expect(api.setProgramModel).not.toHaveBeenCalled();
  });

  it("posts the default worker model on its own", async () => {
    renderModal();
    expect((screen.getByLabelText("worker model") as HTMLSelectElement).value).toBe("claude-sonnet-5");
    fireEvent.change(screen.getByLabelText("worker model"), { target: { value: "claude-opus-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.setProgramWorkerModel).toHaveBeenCalledWith("p1", "claude-opus-5"));
    expect(api.setProgramModel).not.toHaveBeenCalled();
    expect(api.setProgramChatModel).not.toHaveBeenCalled();
  });

  it("saves the wiki merge policy", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText(/merges/i), { target: { value: "propose" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setWikiMergePolicy).toHaveBeenCalledWith("p1", "propose"));
  });

  it("unchecking the wiki opts the program out", async () => {
    renderModal();
    fireEvent.click(screen.getByLabelText("wiki enabled"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.setProgramWikiEnabled).toHaveBeenCalledWith("p1", false));
  });

  it("seeds an opted-out program with the box clear", () => {
    renderModal({ wiki_enabled: false });
    expect((screen.getByLabelText("wiki enabled") as HTMLInputElement).checked).toBe(false);
  });

  it("clearing the cap posts zero", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("max proposed experiments"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setProgramMaxProposed).toHaveBeenCalledWith("p1", 0));
  });

  it("posts nothing on cancel", () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("standing instructions"), { target: { value: "new rules" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.setProgramInstructions).not.toHaveBeenCalled();
  });

  it("shows a teal success toast on a clean save", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("standing instructions"), { target: { value: "new rules" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith(
      expect.objectContaining({ color: "teal", message: "Program settings updated." }),
    ));
  });

  it("warns in yellow when the saved folder doesn't exist yet", async () => {
    vi.mocked(api.setProgramWorkdir).mockResolvedValueOnce({ id: "p1", workdir: "/nope", exists: false });
    renderModal();
    fireEvent.change(screen.getByLabelText("project folder"), { target: { value: "/nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith(
      expect.objectContaining({
        color: "yellow",
        message: "Saved, but /nope doesn't exist yet — agents fall back to the control repo until it does.",
      }),
    ));
  });

  it("seeds server checkboxes from the ledger, one shape each", async () => {
    renderModal();
    expect((await screen.findByLabelText("may run on local") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("may run on gpu1") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("may run on gpu2") as HTMLInputElement).checked).toBe(false);
  });

  it("disables the only-program server's checkbox", async () => {
    renderModal();
    expect((await screen.findByLabelText("may run on gpu1") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("the only program this server takes")).toBeTruthy();
  });

  it("saves the remaining servers after unchecking one", async () => {
    renderModal();
    fireEvent.click(await screen.findByLabelText("may run on local"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setProgramHosts).toHaveBeenCalledWith("p1", ["gpu1"]));
  });

  it("does not call setProgramHosts when the server checkboxes are unchanged", async () => {
    const { onSaved } = renderModal();
    await screen.findByLabelText("may run on local");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(api.setProgramHosts).not.toHaveBeenCalled();
  });

  it("shows the cut-off message in yellow when saving takes work off a server", async () => {
    vi.mocked(api.setProgramHosts).mockResolvedValueOnce({
      capacity: {}, used: {}, available: {}, leases: [], paused: false, hosts: [],
      cut_off: [{ sprint_id: "s1", host: "gpu2" }],
    });
    renderModal();
    fireEvent.click(await screen.findByLabelText("may run on gpu2"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith(
      expect.objectContaining({
        color: "yellow",
        message: "s1 is pinned to gpu2. It keeps its work there and waits until the program is allowed back on that server or the sprint is stopped.",
      }),
    ));
  });

  it("keeps in-progress edits when the program is refetched while open", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <ProgramSettingsModal opened onClose={() => {}} onSaved={() => {}} program={program as never} />
        </QueryClientProvider>
      </MantineProvider>,
    );
    fireEvent.change(screen.getByLabelText("standing instructions"), { target: { value: "mine" } });
    rerender(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <ProgramSettingsModal opened onClose={() => {}} onSaved={() => {}}
            program={{ ...program, instructions: "server copy" } as never} />
        </QueryClientProvider>
      </MantineProvider>,
    );
    expect((screen.getByLabelText("standing instructions") as HTMLTextAreaElement).value).toBe("mine");
  });
});
