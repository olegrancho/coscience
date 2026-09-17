import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { answerEscalation: vi.fn() } }));

import { api, type Sprint, type SprintEscalation } from "../api";
import EscalationPanel from "./EscalationPanel";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function escalation(over: Partial<SprintEscalation> = {}): SprintEscalation {
  return {
    level: "human", by: "agent", at: 1_700_000_000, host: "gpu1",
    what: "The fine-tune script crashed after step 200.",
    tried: "Restarted twice, checked disk space.",
    may_have_broken_something: false,
    needs: "A human to check the CUDA driver.",
    thread_id: "th1", hosts_allowed: ["gpu2", "gpu3"], stop_requested: false,
    ...over,
  };
}

function sprint(over: Partial<Sprint> = {}): Sprint {
  return {
    id: "s1", status: "escalated", title: "", summary: "", goals: "g", priority: 0,
    preemptible: true, resources_required: {}, distributed: false,
    rationale: "", plan: [], program: "p1", results: [], threads: [],
    agent_running: false, started_at: null, error: "", lease: null, model: "m",
    activity: null, votes: { up: 0, down: 0, mine: 0 },
    escalation: escalation(),
    ...over,
  } as Sprint;
}

function renderPanel(s: Sprint, onDone = () => {}) {
  return render(
    <MantineProvider>
      <EscalationPanel sprint={s} onDone={onDone} />
    </MantineProvider>,
  );
}

describe("EscalationPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders a pm-level record: the waiting text AND the human answers (a human may answer at either level)", () => {
    renderPanel(sprint({ escalation: escalation({
      level: "pm", what: "Ran out of ideas.", tried: "Tried three approaches.",
      needs: "Guidance on next steps.", may_have_broken_something: true,
    }) }));
    expect(screen.getByText("Ran out of ideas.")).toBeTruthy();
    expect(screen.getByText("Tried three approaches.")).toBeTruthy();
    expect(screen.getByText("Guidance on next steps.")).toBeTruthy();
    expect(screen.getByText(/may have damaged/i)).toBeTruthy();
    expect(screen.getByText("The PM will answer this on its next cycle")).toBeTruthy();
    expect(screen.getByText("Or answer it yourself now:")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Move to another server" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop sprint" })).toBeTruthy();
  });

  it("stops a pm-level escalation too, after confirm()", async () => {
    vi.mocked(api.answerEscalation).mockResolvedValue({} as Sprint);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel(sprint({ escalation: escalation({ level: "pm" }) }));
    fireEvent.click(screen.getByRole("button", { name: "Stop sprint" }));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.answerEscalation).toHaveBeenCalledWith(
      "s1", { action: "stop", thread_id: "th1" }));
  });

  it("renders a human-level record with the three answers", () => {
    renderPanel(sprint());
    expect(screen.getByText("Needs you")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Move to another server" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop sprint" })).toBeTruthy();
  });

  it("resumes with typed instructions, then calls onDone", async () => {
    vi.mocked(api.answerEscalation).mockResolvedValue({} as Sprint);
    const onDone = vi.fn();
    renderPanel(sprint(), onDone);
    fireEvent.change(screen.getByLabelText("Instructions for the agent"), { target: { value: "go" } });
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(api.answerEscalation).toHaveBeenCalledWith(
      "s1", { action: "resume", instructions: "go", thread_id: "th1" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("moves to another server: picking a host and confirming reallocates", async () => {
    vi.mocked(api.answerEscalation).mockResolvedValue({} as Sprint);
    renderPanel(sprint());
    fireEvent.click(screen.getByRole("button", { name: "Move to another server" }));
    fireEvent.change(screen.getByLabelText("Destination server"), { target: { value: "gpu3" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));
    await waitFor(() => expect(api.answerEscalation).toHaveBeenCalledWith(
      "s1", { action: "reallocate", host: "gpu3", instructions: "", thread_id: "th1" }));
  });

  it("disables 'Move to another server' with no hosts_allowed", () => {
    renderPanel(sprint({ escalation: escalation({ hosts_allowed: [] }) }));
    const btn = screen.getByRole("button", { name: "Move to another server" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toMatch(/no other server this program may use/i);
  });

  it("stops the sprint after confirm()", async () => {
    vi.mocked(api.answerEscalation).mockResolvedValue({} as Sprint);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel(sprint());
    fireEvent.click(screen.getByRole("button", { name: "Stop sprint" }));
    expect(window.confirm).toHaveBeenCalledWith(
      "Stop this sprint? It will be marked failed; a human can resume it later.");
    await waitFor(() => expect(api.answerEscalation).toHaveBeenCalledWith(
      "s1", { action: "stop", thread_id: "th1" }));
  });

  it("does not stop when confirm() is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPanel(sprint());
    fireEvent.click(screen.getByRole("button", { name: "Stop sprint" }));
    expect(api.answerEscalation).not.toHaveBeenCalled();
  });

  it("shows an API error", async () => {
    vi.mocked(api.answerEscalation).mockRejectedValue(new Error("422 bad host"));
    renderPanel(sprint());
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(await screen.findByText(/422 bad host/)).toBeTruthy();
  });

  it("tells the reader replies in the thread are not read by the agent", () => {
    renderPanel(sprint());
    expect(screen.getByText(
      /The agent reads only these instructions; replies in the thread below are for people\./,
    )).toBeTruthy();
  });

  it("shows a pending stop instead of the answer buttons", () => {
    renderPanel(sprint({ escalation: escalation({ stop_requested: true }) }));
    expect(screen.getByText(
      "Stopping — the platform will stop this sprint on its next cycle.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Move to another server" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop sprint" })).toBeNull();
  });
});
