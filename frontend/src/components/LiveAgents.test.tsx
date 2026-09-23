import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

const callLog = vi.fn();
const programs = vi.fn();
const sprints = vi.fn();
vi.mock("../api", () => ({ api: {
  getCallLog: () => callLog(), listPrograms: () => programs(), listSprints: () => sprints(),
} }));

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

import LiveAgents from "./LiveAgents";

const row = (over: Record<string, unknown> = {}) => ({
  id: Math.random().toString(36).slice(2),
  kind: "worker", program: "p2", sprint: "p2-c37", model: "claude-sonnet-5",
  status: "running", cost: null, started_at: Date.now() / 1000 - 240,
  ended_at: null, duration: null, limits_before: null, limits_after: null,
  ...over,
});

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // The hovers are Mantine tooltips (P9), so the rail's theme context is needed here too.
  return render(
    <MantineProvider><QueryClientProvider client={qc}><LiveAgents /></QueryClientProvider></MantineProvider>,
  );
}

beforeEach(() => {
  callLog.mockReset();
  programs.mockReset();
  programs.mockResolvedValue([]);
  sprints.mockReset();
  sprints.mockResolvedValue([]);
});

describe("LiveAgents in the rail", () => {
  it("lists each agent actually calling Claude right now", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ kind: "worker", program: "p2" }),
      row({ kind: "wiki-ingest", program: "p3", sprint: "" }),
    ] });
    // The count sits in its own <b>, so assert on rendered text, not one element.
    const { container } = renderIt();
    await waitFor(() => expect(container.textContent).toMatch(/2\s*agents/i));
    expect(screen.getByText("worker")).toBeTruthy();
    expect(screen.getByText("wiki")).toBeTruthy();
  });

  it("counts one agent in the singular", async () => {
    callLog.mockResolvedValue({ calls: [row()] });
    const { container } = renderIt();
    await waitFor(() => expect(container.textContent).toMatch(/1\s*agent\b/i));
    expect(container.textContent).not.toMatch(/agents/i);
  });

  it("ignores calls that have already finished", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ status: "ok", ended_at: Date.now() / 1000 }),
      row({ status: "rate-limited", ended_at: Date.now() / 1000 }),
    ] });
    renderIt();
    await waitFor(() => expect(screen.getByText(/no agents running/i)).toBeTruthy());
  });

  it("does not count a lost call as still working", async () => {
    // A start with no end past the grace window is a dead process, not an agent.
    callLog.mockResolvedValue({ calls: [row({ status: "lost" })] });
    renderIt();
    await waitFor(() => expect(screen.getByText(/no agents running/i)).toBeTruthy());
  });

  it("shows how long each has been going", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ started_at: Date.now() / 1000 - 300 }),
    ] });
    renderIt();
    await waitFor(() => expect(screen.getByText("5m")).toBeTruthy());
  });

  it("names the program each agent is working on", async () => {
    callLog.mockResolvedValue({ calls: [row({ kind: "pm", program: "p5", sprint: "" })] });
    renderIt();
    await waitFor(() => expect(screen.getByText("p5")).toBeTruthy());
  });

  it("names a worker's experiment, its program and model on hover of its row (P9)", async () => {
    programs.mockResolvedValue([{ id: "p2", title: "Lead Finder optimization", status: "active", goals: "" }]);
    sprints.mockResolvedValue([{ id: "p2-c37", status: "executing", title: "Dock the new ligands" }]);
    callLog.mockResolvedValue({ calls: [row({ program: "p2", sprint: "p2-c37", model: "claude-sonnet-5" })] });
    renderIt();
    fireEvent.mouseEnter((await screen.findByText("worker")).parentElement!);
    expect(await screen.findByText("Dock the new ligands · Lead Finder optimization · Sonnet 5")).toBeTruthy();
  });

  it("lists what every live agent is doing on hover of the count (P9)", async () => {
    programs.mockResolvedValue([{ id: "p3", title: "Abiogenesis", status: "active", goals: "" }]);
    sprints.mockResolvedValue([{ id: "p2-c37", status: "executing", title: "Dock the new ligands" }]);
    callLog.mockResolvedValue({ calls: [
      row({ kind: "worker", program: "p2", sprint: "p2-c37", model: "claude-sonnet-5" }),
      row({ kind: "pm", program: "p3", sprint: "", model: "claude-opus-5" }),
    ] });
    renderIt();
    fireEvent.mouseEnter(await screen.findByText(/agents calling Claude/));
    expect(await screen.findByText("worker: Dock the new ligands · p2 · Sonnet 5")).toBeTruthy();
    expect(screen.getByText("pm: Abiogenesis · Opus 5")).toBeTruthy();
  });

  it("falls back to the slug and raw model id when it cannot name them", async () => {
    callLog.mockResolvedValue({ calls: [row({ program: "p9", sprint: "", model: "claude-new-9" })] });
    renderIt();
    fireEvent.mouseEnter((await screen.findByText("worker")).parentElement!);
    expect(await screen.findByText("p9 · claude-new-9")).toBeTruthy();
  });

  it("renders nothing until the log has loaded, so the rail never flickers", async () => {
    // Same branch the error path takes (`isError || !data` -> null). Asserted via
    // the pending state because a rejected mock surfaces as an unhandled
    // rejection in this harness rather than reaching the component.
    let settle: (v: unknown) => void = () => {};
    callLog.mockReturnValue(new Promise((res) => { settle = res; }));
    renderIt();
    // Not container.textContent: the Mantine provider puts a <style> in the container.
    expect(screen.queryByText(/agent/)).toBeNull();
    settle({ calls: [] });          // never leave a promise pending at teardown
  });
});
