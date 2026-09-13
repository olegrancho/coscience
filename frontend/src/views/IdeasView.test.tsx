import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import IdeasView from "./IdeasView";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  vi.spyOn(api, "getProgram").mockResolvedValue({
    id: "p", title: "P", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", activations: [], last_run: null, instructions: "",
  } as any);
  vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
  vi.spyOn(api, "listGuidance").mockResolvedValue([]);
  vi.spyOn(api, "listUsers").mockResolvedValue([] as any);
});

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p/ideas"]}>
        <Routes><Route path="/programs/:id/ideas" element={<IdeasView />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
}

describe("compress / brainstorm", () => {
  async function clickCompress(reply: Record<string, unknown>) {
    vi.spyOn(api, "pmDirective").mockResolvedValue(
      { program: "p", cycle: 3, submitted: [], ...reply } as any);
    const show = vi.spyOn(notifications, "show").mockImplementation(() => "" as any);
    renderAt();
    fireEvent.click(await screen.findByText("Compress"));
    await waitFor(() => expect(show).toHaveBeenCalled());
    // Not .at(-1): tsconfig targets ES2020, and `npm run build` typechecks tests.
    return show.mock.calls[show.mock.calls.length - 1][0] as { color?: string; message?: string };
  }

  it("says the planner stood down rather than 'nothing to do'", async () => {
    // These buttons force a beat, so they are the same escape hatch as Replan — and
    // a backed-off beat never ran. "Nothing to do this cycle" would describe a
    // healthy pool, not a planner that has given up.
    const n = await clickCompress({ skipped: true, backoff: true });
    expect(String(n.message)).not.toMatch(/Nothing to do/i);
    expect(String(n.message)).toMatch(/failed/i);
    expect(String(n.message)).toMatch(/runs\.jsonl/);
  });

  it("names the global pause instead of blaming an exhausted budget", async () => {
    // Same reason as Replan: a paused platform is not waiting on a usage reset, and
    // "Nothing to do this cycle" would hide the pause entirely.
    const n = await clickCompress({ skipped: true, paused: true });
    expect(String(n.message)).toMatch(/paused/i);
    expect(String(n.message)).toMatch(/resume/i);
    expect(String(n.message)).not.toMatch(/Nothing to do/i);
    expect(n.color).toBe("yellow");
  });

  it("still reports an ordinary quiet cycle as before", async () => {
    const n = await clickCompress({ skipped: true });
    expect(String(n.message)).toMatch(/Nothing to do/i);
  });
});

describe("promote an idea", () => {
  it("opens the proposal pre-filled and submits it as a promotion", async () => {
    window.ResizeObserver = window.ResizeObserver || (class {
      observe() {} unobserve() {} disconnect() {}
    } as any);
    vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [
      { id: "i1", text: "Try rescoring with waters", source: "human", by: "", pinned: true,
        protected: true, threads: [], created_at: 0, demoted: false },
    ] } as any);
    const submit = vi.spyOn(api, "submitSprint").mockResolvedValue({} as any);
    renderAt();

    fireEvent.click(await screen.findByLabelText("make a sprint"));
    const goals = (await screen.findByLabelText("Goals")) as HTMLTextAreaElement;
    expect(goals.value).toBe("Try rescoring with waters");
    fireEvent.click(screen.getByText("Submit proposal"));

    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ id: "p-idea-i1", program: "p", from_idea: "i1" })));
  });
});

describe("draft a promotion with the planner", () => {
  it("fills the proposal from the planner's draft", async () => {
    window.ResizeObserver = window.ResizeObserver || (class {
      observe() {} unobserve() {} disconnect() {}
    } as any);
    vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [
      { id: "i1", text: "Try rescoring with waters", source: "human", by: "", pinned: true,
        protected: true, threads: [], created_at: 0, demoted: false },
    ] } as any);
    const draft = vi.spyOn(api, "draftSprintFromIdea").mockResolvedValue({
      id: "p-waters", title: "Rescore with waters", summary: "S", goals: "G",
      plan: ["a", "b"], priority: 3, rationale: "R" });
    renderAt();

    fireEvent.click(await screen.findByLabelText("make a sprint"));
    fireEvent.click(await screen.findByText("Draft with AI"));

    await waitFor(() =>
      expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Rescore with waters"));
    expect(draft).toHaveBeenCalledWith("p", "i1");
    expect((screen.getByLabelText("Goals") as HTMLTextAreaElement).value).toBe("G");
    expect((screen.getByLabelText("Sprint id") as HTMLInputElement).value).toBe("p-waters");
  });
});
