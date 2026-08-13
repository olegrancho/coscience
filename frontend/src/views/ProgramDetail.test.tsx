import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { notifications } from "@mantine/notifications";
import ProgramDetail from "./ProgramDetail";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as any);
});

function mockProgram(instructions: string) {
  vi.spyOn(api, "getProgram").mockResolvedValue({
    id: "p", title: "P", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", activations: [], last_run: null,
    instructions,
  } as any);
  vi.spyOn(api, "listGuidance").mockResolvedValue([]);
  vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
  vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
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
