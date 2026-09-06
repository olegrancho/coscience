import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const callLog = vi.fn();
vi.mock("../api", () => ({ api: { getCallLog: () => callLog() } }));

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
  // No MantineProvider: LiveAgents is plain markup, so the rail can render it
  // without pulling in a theme context (and its matchMedia requirement).
  return render(
    <QueryClientProvider client={qc}><LiveAgents /></QueryClientProvider>,
  );
}

beforeEach(() => callLog.mockReset());

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

  it("renders nothing until the log has loaded, so the rail never flickers", async () => {
    // Same branch the error path takes (`isError || !data` -> null). Asserted via
    // the pending state because a rejected mock surfaces as an unhandled
    // rejection in this harness rather than reaching the component.
    let settle: (v: unknown) => void = () => {};
    callLog.mockReturnValue(new Promise((res) => { settle = res; }));
    const { container } = renderIt();
    expect(container.textContent).toBe("");
    settle({ calls: [] });          // never leave a promise pending at teardown
  });
});
