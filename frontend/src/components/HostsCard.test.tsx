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
import HostsCard, { cardSlots, hostStatus, slots } from "./HostsCard";

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

describe("hostStatus", () => {
  it("reads working when work is on it and ready when it is idle", () => {
    expect(hostStatus(LOCAL).key).toBe("working");
    expect(hostStatus({ ...LOCAL, leases: 0 }).key).toBe("ready");
    expect(hostStatus({ ...LOCAL, leases: 0 }).detail).toMatch(/nothing running/);
  });

  it("reads offline for a server that is not answering, and says since when", () => {
    const status = hostStatus({ ...REMOTE, leases: 0 });
    expect(status.key).toBe("offline");
    expect(status.detail).toMatch(/not answering since/);
    expect(status.detail).toMatch(/No route to host/);
    expect(status.detail).toMatch(/takes no new work/);      // quiet, so placement stops
  });

  it("does not say a merely failing server has stopped taking work", () => {
    // Failing is the first 30 minutes; only quiet closes it to new grants.
    const failing = { ...REMOTE, health: { ...REMOTE.health!, state: "failing" as const } };
    expect(hostStatus(failing).detail).not.toMatch(/takes no new work/);
  });

  it("lets a human's decision outrank how the server is answering", () => {
    // A removing server that is also offline reads "removing": that is the fact
    // that decides what happens to it next.
    expect(hostStatus({ ...REMOTE, removing: true }).key).toBe("removing");
    expect(hostStatus({ ...REMOTE, drain: true }).key).toBe("draining");
    expect(hostStatus({ ...REMOTE, removing: true }).detail).toMatch(/not answering since/);
  });

  it("says what a removing server waits on, or that it leaves next cycle", () => {
    expect(hostStatus({ ...REMOTE, removing: true, waiting_on: [] }).detail)
      .toMatch(/leaves the pool on the dispatcher's next cycle/);
    expect(hostStatus({
      ...REMOTE, removing: true,
      waiting_on: [{ sprint_id: "p1-c3", status: "executing", reason: "holds a lease here" }],
    }).detail).toMatch(/waiting on p1-c3 \(executing, holds a lease here\)/);
  });

  it("reads unchecked while remote placement is off, and says so", () => {
    const status = hostStatus(REMOTE_OFF);
    expect(status.key).toBe("unchecked");
    expect(status.detail).toMatch(/remote placement is off/);
  });

  it("falls back for an older backend that sends no health at all", () => {
    const old = { ...REMOTE_OFF, health: undefined };
    expect(hostStatus(old).key).toBe("unchecked");
    expect(hostStatus({ ...LOCAL, health: undefined, leases: 0 }).key).toBe("ready");
  });
});

describe("slots", () => {
  it("draws one pip per core, filled up to what is reserved", () => {
    const s = slots(24, 20);
    expect(s.pips.length).toBe(24);
    expect(s.pips.filter((p) => p === "full").length).toBe(20);
    expect(s.label).toBe("20/24");
    expect(s.per).toBe(1);
  });

  it("groups cores past the pip cap instead of drawing an uncountable row", () => {
    const s = slots(128, 32);
    expect(s.pips.length).toBeLessThanOrEqual(32);
    expect(s.per).toBe(4);
    expect(s.label).toBe("32/128");          // the number stays exact
  });

  it("says nothing is declared rather than drawing an empty row", () => {
    expect(slots(0, 0)).toEqual({ pips: [], label: "—", per: 1 });
  });

  it("rounds a partial pip up, so any use at all shows", () => {
    expect(slots(24, 0.5).pips.filter((p) => p === "full").length).toBe(1);
  });
});

describe("cardSlots", () => {
  it("fills a whole card, half-fills a shared one and leaves a free one empty", () => {
    expect(cardSlots(LOCAL)).toEqual([{ state: "full", title: "GPU 0: in use" }]);
    expect(cardSlots(REMOTE)).toEqual([
      { state: "part", title: "GPU 0 — X, 10.8 GB: 6 GB lent out in shares" }]);
    const free = { ...REMOTE, gpus: [{ index: 0, model: "X", vram_gb: 10.8, whole: false, shared_gb: 0 }] };
    expect(cardSlots(free)[0].state).toBe("empty");
  });
});

describe("HostsCard", () => {
  it("lists every server by name, with its programs and status", () => {
    renderCard();
    expect(screen.getByText("local")).toBeTruthy();
    expect(screen.getByText("gpu1")).toBeTruthy();
    expect(screen.getByText("all")).toBeTruthy();
    expect(screen.getByText("p2")).toBeTruthy();
    expect(screen.getByText("working")).toBeTruthy();
    expect(screen.getByText("offline")).toBeTruthy();
  });

  it("no longer shows how a server is reached, nor a wall of offers and use", () => {
    renderCard();
    expect(screen.queryByText("this machine")).toBeNull();
    expect(screen.queryByText(/10 CPU cores · 50 GB memory/)).toBeNull();
    expect(screen.queryByText("4 of 10 CPU cores")).toBeNull();
  });

  it("shows memory under the cores only where a server declares it", () => {
    renderCard();
    expect(screen.getByText("0/50 GB memory")).toBeTruthy();   // gpu1 declares memory
    expect(screen.queryByText(/GB memory/)).toBeTruthy();
    expect(screen.getAllByText(/GB memory/).length).toBe(1);   // local declares none
  });

  it("opens the config dialog by clicking anywhere on the row", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Configure gpu1" }));
    expect(await screen.findByText("Configure gpu1")).toBeTruthy();
    expect((screen.getByLabelText(/^SSH target/) as HTMLInputElement).value).toBe("gpu1");
  });

  it("opens the config dialog from the keyboard", async () => {
    renderCard();
    fireEvent.keyDown(screen.getByRole("button", { name: "Configure local" }), { key: "Enter" });
    expect(await screen.findByText("Configure this machine")).toBeTruthy();
  });

  it("keeps no Remove or Keep button on the row itself (O12)", () => {
    renderCard([], [LOCAL, { ...REMOTE, removing: true }]);
    expect(screen.queryByRole("button", { name: "Remove gpu1" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Keep gpu1" })).toBeNull();
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

  it("lists run directories left on a server, naming one no sprint explains", () => {
    renderCard();
    expect(screen.getByText(/~\/coscience-runs\/s9/)).toBeTruthy();
    renderCard([], [LOCAL, {
      ...REMOTE,
      leftover: [{ sprint_id: "", status: "unknown", path: "~/coscience-runs/junk" }],
    }]);
    expect(screen.getByText(/~\/coscience-runs\/junk \(no sprint record\)/)).toBeTruthy();
  });

  it("warns about leases on servers that left the pool", () => {
    renderCard([], [LOCAL, REMOTE], [{ sprint_id: "s4", host: "old1", listed: false }]);
    expect(screen.getByText(/s4 still holds a lease on old1, which is no longer in the pool/)).toBeTruthy();
  });

  it("warns differently about a stranded lease on a server that is merely not taking work", () => {
    renderCard([], [LOCAL, REMOTE], [{ sprint_id: "s4", host: "gpu1", listed: true }]);
    expect(screen.getByText(/s4 still holds a lease on gpu1, which takes no work while remote placement is off/))
      .toBeTruthy();
  });
});
