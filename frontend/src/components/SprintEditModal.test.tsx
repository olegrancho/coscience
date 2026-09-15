import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { editSprint: vi.fn().mockResolvedValue({}) } }));

import { api, type Sprint } from "../api";
import SprintEditModal from "./SprintEditModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function sprint(over: Partial<Sprint> = {}): Sprint {
  return {
    id: "s1", status: "proposed", title: "", summary: "", goals: "g", priority: 0,
    preemptible: true, resources_required: { cpu: 4, tpu: 2 }, distributed: false,
    rationale: "", plan: ["a"], program: "p1", results: [], threads: [],
    agent_running: false, started_at: null, error: "", lease: null, model: "m",
    activity: null, votes: { up: 0, down: 0, mine: 0 }, ...over,
  } as Sprint;
}

function renderModal(s: Sprint) {
  return render(
    <MantineProvider>
      <SprintEditModal sprint={s} opened onClose={() => {}} onDone={() => {}} />
    </MantineProvider>,
  );
}

describe("SprintEditModal compute fields", () => {
  beforeEach(() => vi.clearAllMocks());

  it("pre-fills the compute fields from the request", () => {
    renderModal(sprint({ resources_required: { cpu: 4, gpu: 1, gpu_vram_gb: 12 } }));
    expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("4");
    expect((screen.getByLabelText("GPUs") as HTMLInputElement).value).toBe("1");
    expect((screen.getByLabelText("VRAM per GPU (GB)") as HTMLInputElement).value).toBe("12");
  });

  it("sends the reshaped request and keeps other resources", async () => {
    renderModal(sprint());
    fireEvent.change(screen.getByLabelText("Memory (GB)"), { target: { value: "16" } });
    // Regex, not exact: the Switch's description text now lives inside the same
    // <label>, so an exact match on the label alone no longer matches (Fix D).
    fireEvent.click(screen.getByLabelText(/May split across hosts/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.editSprint).toHaveBeenCalled());
    expect(api.editSprint).toHaveBeenCalledWith(
      "s1", { resources_required: { cpu: 4, memory_gb: 16, tpu: 2 }, distributed: true });
  });

  it("sends nothing about compute when nothing changed", async () => {
    renderModal(sprint());
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.editSprint).toHaveBeenCalled());
    expect(api.editSprint).toHaveBeenCalledWith("s1", {});
  });

  it("re-reads the request each time it opens, so saving priority never restores an old one", async () => {
    const before = sprint({ resources_required: { cpu: 4 } });
    const { rerender } = render(
      <MantineProvider>
        <SprintEditModal sprint={before} opened={false} onClose={() => {}} onDone={() => {}} />
      </MantineProvider>,
    );
    const after = sprint({ resources_required: { cpu: 8 }, distributed: true });
    rerender(
      <MantineProvider>
        <SprintEditModal sprint={after} opened onClose={() => {}} onDone={() => {}} />
      </MantineProvider>,
    );
    await waitFor(() =>
      expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("8"));
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.editSprint).toHaveBeenCalled());
    expect(api.editSprint).toHaveBeenCalledWith("s1", { priority: 3 });
  });
});
