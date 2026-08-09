import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    setProgramModel: vi.fn().mockResolvedValue({}),
    setProgramWorkdir: vi.fn().mockResolvedValue({ id: "p1", workdir: "/tmp/proj2", exists: true }),
    setProgramMaxProposed: vi.fn().mockResolvedValue({}),
    setProgramInstructions: vi.fn().mockResolvedValue({}),
    listDirs: vi.fn().mockResolvedValue({ path: null, parent: null, roots: [], entries: [] }),
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
    expect(onSaved).toHaveBeenCalled();
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
