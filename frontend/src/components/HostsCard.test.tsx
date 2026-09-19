import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    probeHost: vi.fn(), confirmHost: vi.fn(), removeHost: vi.fn(), keepHost: vi.fn(),
    updateHost: vi.fn(), detectLocal: vi.fn(), setCapacity: vi.fn(),
    listPrograms: vi.fn().mockResolvedValue([]),
  },
}));

import { api } from "../api";
import type { LedgerHost, StrandedLease } from "../api";
import HostsCard, { hostOffer } from "./HostsCard";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const LOCAL: LedgerHost = {
  name: "local", ssh: "", placeable: true, programs: null, run_root: "",
  capacity: { cpu: 24, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "", vram_gb: null, whole: true, shared_gb: 0 }],
  drain: false, removing: false, waiting_on: [],
  health: { state: "local", checked_at: 0, last_ok: 0, fail_since: 0, reason: "" },
  used: { cpu: 3 }, leases: 1, leftover: [],
};
const REMOTE: LedgerHost = {
  // placeable: true — this host is being actively health-checked (COSCIENCE_ALLOW_REMOTE
  // is on) and merely not answering right now; distinct from the non-placeable case
  // below, where remote launch itself is off and health is never consulted.
  name: "gpu1", ssh: "gpu1", placeable: true, programs: ["p2"], run_root: "~/coscience-runs",
  capacity: { cpu: 10, memory_gb: 50, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "X", vram_gb: 10.8, whole: false, shared_gb: 6 }],
  drain: false, removing: false, waiting_on: [],
  health: {
    state: "quiet", checked_at: 1_700_000_100, last_ok: 1_699_990_000,
    fail_since: 1_700_000_000, reason: "No route to host",
  },
  used: { cpu: 4 }, leases: 1,
  leftover: [{ sprint_id: "s9", status: "done", path: "~/coscience-runs/s9" }],
};
const REMOTE_OFF: LedgerHost = {
  // Fix C: remote placement is off (COSCIENCE_ALLOW_REMOTE unset) — the service
  // never checks this host, so it always sends "unchecked", not "not checked yet".
  name: "gpu2", ssh: "gpu2", placeable: false, programs: [], run_root: "~/runs",
  capacity: { cpu: 8 }, available: {}, gpus: [],
  drain: false, removing: false, waiting_on: [],
  health: { state: "unchecked", checked_at: 0, last_ok: 0, fail_since: 0, reason: "" },
  used: {}, leases: 0, leftover: [],
};

function renderCard(errors: string[] = [], hosts: LedgerHost[] = [LOCAL, REMOTE], stranded: StrandedLease[] = []) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <HostsCard hosts={hosts} errors={errors} stranded={stranded} localCapacity={LOCAL.capacity} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("hostOffer", () => {
  it("reads cores, memory and each card", () => {
    expect(hostOffer(LOCAL)).toBe("24 CPU cores · GPU (VRAM not declared)");
    expect(hostOffer(REMOTE)).toBe("10 CPU cores · 50 GB memory · 10.8 GB GPU");
  });
});

describe("HostsCard", () => {
  it("lists every server with how it is reached, what it offers and whether it takes work", () => {
    renderCard();
    // "Reached by" reads "this machine" for the local host (no ssh); "Status"
    // reads "takes work" for health.state "local" — distinct strings, so
    // "this machine" appears exactly once.
    expect(screen.getByText("takes work")).toBeTruthy();
    expect(screen.getAllByText("this machine").length).toBe(1);
    expect(screen.getByText("10 CPU cores · 50 GB memory · 10.8 GB GPU")).toBeTruthy();
    expect(screen.getByText("p2")).toBeTruthy();
  });

  it("labels each server's program access", () => {
    renderCard([], [LOCAL, REMOTE, REMOTE_OFF]);
    expect(screen.getByText("all")).toBeTruthy();
    expect(screen.getByText("p2")).toBeTruthy();
    expect(screen.getByText("none")).toBeTruthy();
  });

  it("shows host errors from the pool file", () => {
    renderCard(["hosts.typo: needs ssh (an ssh alias or user@host)"]);
    expect(screen.getByText("hosts.typo: needs ssh (an ssh alias or user@host)")).toBeTruthy();
  });

  it("opens the add-server dialog", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(await screen.findByLabelText("SSH target")).toBeTruthy();
  });

  it("says how each server is answering", () => {
    renderCard();
    expect(screen.getByText(/not answering since/)).toBeTruthy();
    expect(screen.getByText(/No route to host/)).toBeTruthy();
    expect(screen.getByText(/takes no new work/)).toBeTruthy();
  });

  it("shows what is in use on each server, including shared VRAM", () => {
    renderCard();
    expect(screen.getByText("4 of 10 CPU cores")).toBeTruthy();
    expect(screen.getByText("GPU 0: 6 of 10.8 GB shared")).toBeTruthy();
  });

  it("lists run directories finished sprints left on a server", () => {
    renderCard();
    expect(screen.getByText(/~\/coscience-runs\/s9/)).toBeTruthy();
  });

  it("says plainly when a run directory on the server matches no sprint (O20)", () => {
    renderCard([], [LOCAL, {
      ...REMOTE,
      leftover: [{ sprint_id: "", status: "unknown", path: "~/coscience-runs/junk" }],
    }]);
    expect(screen.getByText(/~\/coscience-runs\/junk \(no sprint record\)/)).toBeTruthy();
  });

  it("shows one enabled Remove button and no Drain or Take back button for an untouched remote server", () => {
    renderCard();
    const btn = screen.getByRole("button", { name: "Remove gpu1" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(screen.queryByRole("button", { name: "Drain gpu1" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Take back gpu1" })).toBeNull();
  });

  it("removes a server, confirming with what waiting for the pool means (M5, O15)", async () => {
    vi.mocked(api.removeHost).mockResolvedValue({} as never);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Remove gpu1" }));
    expect(confirmSpy.mock.calls[0][0]).toMatch(/takes no new work now and leaves the pool as soon as nothing runs there/);
    expect(confirmSpy.mock.calls[0][0]).toMatch(/~\/coscience-runs\/s9/);
    await waitFor(() => expect(api.removeHost).toHaveBeenCalledWith("gpu1"));
  });

  it("does not remove when the confirm is declined", () => {
    vi.mocked(api.removeHost).mockClear();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Remove gpu1" }));
    expect(api.removeHost).not.toHaveBeenCalled();
  });

  it("shows Keep instead of Remove for a server marked removing, and keeps it", async () => {
    vi.mocked(api.keepHost).mockResolvedValue({} as never);
    const removing = { ...REMOTE, removing: true };
    renderCard([], [LOCAL, removing]);
    expect(screen.queryByRole("button", { name: "Remove gpu1" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Keep gpu1" }));
    await waitFor(() => expect(api.keepHost).toHaveBeenCalledWith("gpu1"));
  });

  it("shows Keep instead of Remove for a legacy drained server, and keeps it", async () => {
    vi.mocked(api.keepHost).mockResolvedValue({} as never);
    const drained = { ...REMOTE, drain: true };
    renderCard([], [LOCAL, drained]);
    expect(screen.queryByRole("button", { name: "Remove gpu1" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Keep gpu1" }));
    await waitFor(() => expect(api.keepHost).toHaveBeenCalledWith("gpu1"));
  });

  it("says a removing server with nothing to wait on leaves on the dispatcher's next cycle", () => {
    const removing = { ...REMOTE, removing: true, waiting_on: [] };
    renderCard([], [LOCAL, removing]);
    expect(screen.getByText(/removing — leaves the pool on the dispatcher's next cycle/)).toBeTruthy();
  });

  it("says what a removing server is still waiting on", () => {
    const removing = {
      ...REMOTE, removing: true,
      waiting_on: [{ sprint_id: "p1-c3", status: "executing", reason: "holds a lease here" }],
    };
    renderCard([], [LOCAL, removing]);
    expect(screen.getByText(/removing — waiting on p1-c3 \(executing, holds a lease here\)/)).toBeTruthy();
  });

  it("warns about leases on servers that left the pool", () => {
    renderCard([], [LOCAL, REMOTE], [{ sprint_id: "s4", host: "old1", listed: false }]);
    expect(screen.getByText(/s4 still holds a lease on old1, which is no longer in the pool/)).toBeTruthy();
  });

  it("warns differently about a stranded lease on a server that is merely not taking work (Fix C)", () => {
    renderCard([], [LOCAL, REMOTE], [{ sprint_id: "s4", host: "gpu1", listed: true }]);
    expect(screen.getByText(/s4 still holds a lease on gpu1, which takes no work while remote placement is off/))
      .toBeTruthy();
  });

  it("reads 'waits for remote launch' for a checked-but-not-placeable host (Fix C)", () => {
    renderCard([], [LOCAL, REMOTE_OFF]);
    expect(screen.getByText("waits for remote launch")).toBeTruthy();
  });

  it("says 'takes no new work' once for a quiet, drained host (M8b)", () => {
    const drained = { ...REMOTE, drain: true };
    renderCard([], [LOCAL, drained]);
    const text = screen.getByText(/not answering since/).textContent ?? "";
    expect(text.match(/takes no new work/g)?.length).toBe(1);
    expect(text).toMatch(/draining — takes no new work/);
  });

  it("offers neither Remove nor Keep for this machine", () => {
    renderCard();
    expect(screen.queryByRole("button", { name: "Remove local" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Keep local" })).toBeNull();
  });

  it("gives every row a Config button", () => {
    renderCard();
    expect(screen.getByRole("button", { name: "Configure local" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Configure gpu1" })).toBeTruthy();
  });

  it("opens the dialog to edit a remote server", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Configure gpu1" }));
    expect(await screen.findByText("Configure gpu1")).toBeTruthy();
    expect((screen.getByLabelText(/^SSH target/) as HTMLInputElement).value).toBe("gpu1");
  });

  it("opens the dialog to configure this machine", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Configure local" }));
    expect(await screen.findByText("Configure this machine")).toBeTruthy();
    expect(screen.queryByLabelText(/^SSH target/)).toBeNull();
  });

  it("falls back for an older backend that sends no health, use or leftover fields", () => {
    const OLD_REMOTE: LedgerHost = {
      name: "oldgpu", ssh: "oldgpu", placeable: false, programs: null, run_root: "~/runs",
      capacity: { cpu: 8 }, available: {}, gpus: [], removing: false, waiting_on: [],
    };
    renderCard([], [LOCAL, OLD_REMOTE]);
    expect(screen.getByText("waits for remote launch")).toBeTruthy();
    const remove = screen.getByRole("button", { name: "Remove oldgpu" }) as HTMLButtonElement;
    expect(remove.disabled).toBe(false);
  });
});
