import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    probeHost: vi.fn(), confirmHost: vi.fn(), removeHost: vi.fn(), keepHost: vi.fn(),
    updateHost: vi.fn(), detectLocal: vi.fn(), setCapacity: vi.fn(),
    listPrograms: vi.fn().mockResolvedValue([
      { id: "p2", title: "Lead Finder optimization", status: "active", goals: "" },
      { id: "p4", title: "Test", status: "paused", goals: "" },
    ]),
  },
}));

import { api } from "../api";
import type { LedgerHost, StrandedLease } from "../api";
import HostsCard, { cardSlots, hostLabel, hostStatus, programsCell, slots } from "./HostsCard";

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

  it("sums run directories into one line under the table, paths on hover", async () => {
    // They used to be a paragraph under every server, repeating the same sentence.
    renderCard([], [LOCAL, {
      ...REMOTE,
      leftover: [
        { sprint_id: "s9", status: "done", path: "~/coscience-runs/s9" },
        { sprint_id: "", status: "unknown", path: "~/coscience-runs/tools" },
      ],
    }]);
    const line = screen.getByText(/Run directories no sprint is using/);
    expect(line.textContent).toMatch(/2 on gpu1/);
    expect(line.textContent).not.toMatch(/coscience-runs/);      // compressed, not listed
    fireEvent.mouseEnter(line);
    expect(await screen.findByText(/~\/coscience-runs\/s9 \(done\)/)).toBeTruthy();
    expect(screen.getByText(/~\/coscience-runs\/tools \(no sprint record\)/)).toBeTruthy();
  });

  it("says nothing at all when no server holds anything unused", () => {
    renderCard([], [LOCAL, { ...REMOTE, leftover: [] }]);
    expect(screen.queryByText(/Run directories no sprint is using/)).toBeNull();
  });

  it("gives every row the same height, memory line or not", () => {
    const { container } = renderCard();
    const cells = container.querySelectorAll("tbody tr td:nth-child(2) > div");
    expect(cells.length).toBe(2);
    cells.forEach((c) => {
      const style = (c as HTMLElement).style;
      expect(style.minHeight).toBe("32px");
      // ...and the content is centred in it, so a server with no memory line
      // does not read as a row with a blank line hanging under it.
      expect(style.justifyContent).toBe("center");
    });
  });

  it("names each allowed program on hover, and hides the ones that cannot take work", async () => {
    const both = { ...REMOTE, programs: ["p2", "p4"] };
    renderCard([], [LOCAL, both]);
    // p4 is paused, so the cell shows p2 alone and the hover says where p4 went.
    const cell = await screen.findByText("p2");
    fireEvent.mouseEnter(cell);
    const tip = await screen.findByText(/p2 — Lead Finder optimization/);
    expect(tip.textContent).toMatch(/not shown \(paused or closed\): p4 — Test/);
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

describe("programsCell", () => {
  const PROGRAMS = [
    { id: "p2", title: "Lead Finder optimization", status: "active", goals: "" },
    { id: "p4", title: "Test", status: "paused", goals: "" },
    { id: "p5", title: "Kernels", status: "closed", goals: "" },
  ];

  it("shows only programs that can take work, and names the rest on hover", () => {
    const cell = programsCell({ ...REMOTE, programs: ["p2", "p4", "p5"] }, PROGRAMS);
    expect(cell.text).toBe("p2");
    expect(cell.title).toMatch(/p2 — Lead Finder optimization/);
    expect(cell.title).toMatch(/not shown \(paused or closed\): p4 — Test, p5 — Kernels/);
  });

  it("does not claim 'none' when the list holds only paused or closed programs", () => {
    // "none" means the server runs nothing by decision; this is a different fact.
    const cell = programsCell({ ...REMOTE, programs: ["p4"] }, PROGRAMS);
    expect(cell.text).toBe("—");
    expect(cell.title).toMatch(/no program that can take work/);
  });

  it("reads all for a server that never set a list, and none for an empty one", () => {
    expect(programsCell({ ...REMOTE, programs: null }, PROGRAMS).text).toBe("all");
    expect(programsCell({ ...REMOTE, programs: null }, PROGRAMS).title)
      .toMatch(/every program:\np2 — Lead Finder optimization/);
    expect(programsCell({ ...REMOTE, programs: [] }, PROGRAMS).text).toBe("none");
  });

  it("shows every id while the program list is still loading", () => {
    // Filtering against a list that has not arrived would briefly show the wrong
    // access — the same race that cut a live server off in O14.
    expect(programsCell({ ...REMOTE, programs: ["p2", "p4"] }, undefined).text).toBe("p2, p4");
  });
});

describe("display names (O12)", () => {
  it("calls a server what a human called it, with the filed-under name on hover", async () => {
    renderCard([], [{ ...LOCAL, label: "Avatar" }, REMOTE]);
    const shown = screen.getByText("Avatar");
    expect(screen.queryByText("local")).toBeNull();
    fireEvent.mouseEnter(shown);
    expect(await screen.findByText(/filed under "local"/)).toBeTruthy();
  });

  it("goes by the name when nobody set one, with no tooltip to chase", () => {
    renderCard();
    expect(screen.getByText("local")).toBeTruthy();
    expect(screen.getByText("gpu1")).toBeTruthy();
  });

  it("ignores a display name that is only spaces", () => {
    expect(hostLabel({ ...LOCAL, label: "   " })).toBe("local");
    expect(hostLabel({ ...LOCAL, label: undefined })).toBe("local");
    expect(hostLabel({ ...LOCAL, label: "Avatar" })).toBe("Avatar");
  });
});
