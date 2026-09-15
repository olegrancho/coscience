import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

const ledger = vi.fn();
const callLog = vi.fn();
vi.mock("../api", () => ({
  api: {
    getLedger: () => ledger(),
    getUsage: () => Promise.resolve(null),
    getCallLog: () => callLog(),
    setCapacity: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false }),
    setPause: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: true }),
  },
}));

import { api } from "../api";
import Ledger from "./Ledger";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter><Ledger /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("Compute page", () => {
  it("warns that agents are unbounded when no worker cap is set", async () => {
    ledger.mockResolvedValue({ capacity: { cpu: 16 }, used: { cpu: 2 }, available: { cpu: 14 }, leases: [], paused: false });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no worker cap/i)).toBeTruthy());
  });

  it("does not warn once a worker cap exists", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: { cpu: 2, workers: 1 },
      available: { cpu: 14, workers: 0 }, leases: [], paused: false,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/capacity in use/i)).toBeTruthy());
    expect(screen.queryByText(/no worker cap/i)).toBeNull();
  });

  it("warns that housekeeping is unbounded when no housekeepers cap is set", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: {}, available: {}, leases: [], paused: false,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no housekeeping cap/i)).toBeTruthy());
  });

  it("does not warn once a housekeepers cap exists", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1, housekeepers: 1 },
      used: { housekeepers: 1 }, available: { housekeepers: 0 }, leases: [], paused: false,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/capacity in use/i)).toBeTruthy());
    expect(screen.queryByText(/no housekeeping cap/i)).toBeNull();
  });

  it("lists Claude calls with what they cost and how they ended", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    callLog.mockResolvedValue({ calls: [{
      id: "a1", kind: "wiki-ingest", program: "p3", sprint: "", model: "claude-opus-4-6",
      status: "rate-limited", cost: 2.25, started_at: 1788560000, ended_at: 1788560394,
      duration: 394, limits_before: { pct: 61, resets: "Fri 21:19" },
      limits_after: { pct: 116, resets: "Fri 21:19" },
    }] });
    renderPage();
    // "wiki-ingest" appears twice: once as a row cell, once as a filter option.
    await waitFor(() => expect(screen.getAllByText("wiki-ingest").length).toBeGreaterThan(0));
    expect(screen.getByText("rate-limited")).toBeTruthy();
    expect(screen.getByText("claude-opus-4-6")).toBeTruthy();
    expect(screen.getByText("$2.25")).toBeTruthy();
    expect(screen.getByText("61% → 116%")).toBeTruthy();
  });

  it("says so when no call has been recorded yet", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    callLog.mockResolvedValue({ calls: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no claude calls recorded yet/i)).toBeTruthy());
  });

  it("opens the edit modal", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: {}, available: {}, leases: [], paused: false,
    });
    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /edit capacity/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /edit capacity/i }));
    await waitFor(() => expect(screen.getByLabelText("workers capacity")).toBeTruthy());
  });

  it("offers Pause while the platform is running", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    renderPage();
    expect(await screen.findByRole("button", { name: /pause/i })).toBeTruthy();
  });

  it("pauses the platform when Pause is clicked", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /pause/i }));
    await waitFor(() => expect(api.setPause).toHaveBeenCalledWith(true));
  });

  it("shows what is still finishing while paused", async () => {
    ledger.mockResolvedValue({
      capacity: {}, used: {}, available: {},
      leases: [{ id: "l1", sprint_id: "p1-c0-a", amounts: { workers: 1 } }], paused: true,
    });
    renderPage();
    expect(await screen.findByText(/1 still finishing/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /resume/i })).toBeTruthy();
  });

  it("says nothing is running once the drain completes", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: true });
    renderPage();
    expect(await screen.findByText(/nothing running/i)).toBeTruthy();
  });

  it("edits this machine's capacity, not the pool total, once remote servers take work", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 40, workers: 2 }, local_capacity: { cpu: 24, workers: 2 },
      used: {}, available: {}, leases: [], paused: false, host_errors: [],
      hosts: [
        { name: "local", ssh: "", placeable: true, programs: [], run_root: "", capacity: { cpu: 24 }, available: {}, gpus: [] },
        { name: "gpu1", ssh: "gpu1", placeable: true, programs: [], run_root: "~/runs", capacity: { cpu: 16 }, available: {}, gpus: [] },
      ],
    });
    renderPage();
    expect(await screen.findByText(/Edit capacity changes this machine only/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit capacity" }));
    expect((await screen.findByLabelText("cpu capacity") as HTMLInputElement).value).toBe("24");
  });

  it("shows which server a running experiment is on", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 40 }, used: { cpu: 4 }, available: {}, paused: false, host_errors: [], hosts: [],
      leases: [{ id: "l1", sprint_id: "s1", amounts: { cpu: 4 }, host: "gpu1" }],
    });
    renderPage();
    expect(await screen.findByText(/on gpu1/)).toBeTruthy();
  });
});

describe("Compute page steppers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.setCapacity).mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    ledger.mockResolvedValue({
      capacity: { cpu: 16, workers: 1 }, used: { cpu: 2, workers: 0 },
      available: { cpu: 14, workers: 1 }, leases: [], paused: false,
    });
  });
  afterEach(() => vi.useRealTimers());

  const readout = (label: string) =>
    screen.getByLabelText(`increase ${label}`).parentElement!.parentElement!.textContent;

  it("shows the new number at once, without saving yet", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("increase cpu")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("increase cpu"));
    expect(readout("cpu")).toContain("2 / 17");
    expect(api.setCapacity).not.toHaveBeenCalled();
  });

  it("saves once, with the whole map, after the debounce", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("increase cpu")).toBeTruthy());
    vi.useFakeTimers();
    fireEvent.click(screen.getByLabelText("increase cpu"));
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(api.setCapacity).toHaveBeenCalledTimes(1);
    expect(api.setCapacity).toHaveBeenCalledWith({ cpu: 17, workers: 1 });
  });

  it("collapses rapid clicks into one save", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("increase cpu")).toBeTruthy());
    vi.useFakeTimers();
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByLabelText("increase cpu"));
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(api.setCapacity).toHaveBeenCalledTimes(1);
    expect(api.setCapacity).toHaveBeenCalledWith({ cpu: 20, workers: 1 });
  });

  it("dims an unsaved number and undims it once saved", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("increase cpu")).toBeTruthy());
    vi.useFakeTimers();
    fireEvent.click(screen.getByLabelText("increase cpu"));
    expect(screen.getByText("2 / 17").style.opacity).toBe("0.45");
    await act(async () => { vi.advanceTimersByTime(1000); });
    vi.useRealTimers();
    await waitFor(() => expect(screen.getByText("2 / 16").style.opacity).toBe(""));
  });

  it("can't take a resource below zero", async () => {
    ledger.mockResolvedValue({
      capacity: { workers: 0 }, used: { workers: 0 }, available: { workers: 0 }, leases: [], paused: false,
    });
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("decrease workers")).toBeTruthy());
    expect((screen.getByLabelText("decrease workers") as HTMLButtonElement).disabled).toBe(true);
  });

  it("reverts and explains when the save is rejected", async () => {
    vi.mocked(api.setCapacity).mockRejectedValue(new Error("422 capacity can't be negative"));
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("increase cpu")).toBeTruthy());
    vi.useFakeTimers();
    fireEvent.click(screen.getByLabelText("increase cpu"));
    await act(async () => { vi.advanceTimersByTime(1000); });
    vi.useRealTimers();
    await waitFor(() => expect(screen.getByText(/422 capacity can't be negative/)).toBeTruthy());
    expect(readout("cpu")).toContain("2 / 16");
  });
});
