import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { probeHost: vi.fn(), confirmHost: vi.fn().mockResolvedValue({}) } }));

import { api } from "../api";
import AddHostModal from "./AddHostModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const OK_PROBE = {
  name: "gpu1", ok: true, error: "",
  facts: { os: "Example Linux 9", cpu_model: "Example CPU", threads: 12, mem_total_kb: 65000000 },
  probed_at: 1,
  declared: { ssh: "gpu1", run_root: "~/coscience-runs", shared: false, programs: [], owner: "", notes: "" },
  checks: [{ name: "rsync both ways", ok: true, detail: "" }],
  warnings: ["glibc 2.17 is old: many current Python wheels and binaries will not run"],
  proposal: { capacity: { cpu: 12, memory_gb: 55 }, gpus: [{ model: "X", vram_gb: 10.8 }] },
};

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <AddHostModal opened onClose={() => {}} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function declare() {
  fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "gpu1" } });
  fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu1" } });
}

describe("AddHostModal", () => {
  beforeEach(() => vi.clearAllMocks());

  it("probes with what the human declared", async () => {
    vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
    renderModal();
    declare();
    fireEvent.change(screen.getByLabelText(/^Programs allowed/), { target: { value: "p2, p5" } });
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
    expect(api.probeHost).toHaveBeenCalledWith({
      name: "gpu1", ssh: "gpu1", run_root: "~/coscience-runs", shared: false,
      programs: ["p2", "p5"], owner: "", notes: "" });
  });

  it("shows the checks and warnings, and confirms the adjusted offer", async () => {
    vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(await screen.findByText(/rsync both ways/)).toBeTruthy();
    expect(screen.getByText(/glibc 2.17 is old/)).toBeTruthy();
    expect(screen.getByText("Example Linux 9 · Example CPU · 12 threads · 62 GB memory")).toBeTruthy();
    expect((screen.getByLabelText("CPU cores offered") as HTMLInputElement).value).toBe("12");
    fireEvent.change(screen.getByLabelText("CPU cores offered"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to the pool" }));
    await waitFor(() => expect(api.confirmHost).toHaveBeenCalled());
    expect(api.confirmHost).toHaveBeenCalledWith({ name: "gpu1", capacity: { cpu: 10, memory_gb: 55 }, probed_at: 1 });
  });

  it("offers nothing to confirm when a check failed", async () => {
    vi.mocked(api.probeHost).mockResolvedValue({
      ...OK_PROBE, checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
    } as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(await screen.findByText(/Fix the failed checks/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
  });

  it("explains a failed probe and offers nothing to confirm", async () => {
    vi.mocked(api.probeHost).mockResolvedValue({
      ...OK_PROBE, ok: false, checks: [], warnings: [], proposal: {},
      error: "the server's host key is not known yet: connect once by hand (ssh gpu1), accept the key, then probe again",
    } as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(await screen.findByText(/connect once by hand/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
  });

  it("forgets the probe when a declared field changes, so nothing unchecked is added", async () => {
    vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(await screen.findByRole("button", { name: "Add to the pool" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
    expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
    expect(screen.queryByText(/rsync both ways/)).toBeNull();
  });

  it("ignores a probe that finishes after a declared field changed", async () => {
    let finish: (value: unknown) => void = () => {};
    vi.mocked(api.probeHost).mockImplementation(() => new Promise((resolve) => { finish = resolve; }) as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
    await act(async () => { finish(OK_PROBE); });
    expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
    expect(screen.queryByText(/rsync both ways/)).toBeNull();
  });
});
