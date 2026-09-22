import { describe, it, expect } from "vitest";
import { diskPulse, pulseCounts } from "./pulseCounts";

const programs = [
  { id: "p2", status: "active" },
  { id: "p9", status: "paused" },
];

describe("pulseCounts", () => {
  it("counts approved and queued sprints as waiting, apart from the ones that can never start", () => {
    const c = pulseCounts(programs, [
      { id: "p2-a", program: "p2", status: "approved" },
      { id: "p2-b", program: "p2", status: "queued" },
      { id: "p2-c", program: "p2", status: "queued", unrunnable: "needs cpu 24 but capacity is 16" },
      { id: "p2-d", program: "p2", status: "proposed" },
      { id: "p2-e", program: "p2", status: "executing" },
      { id: "p9-f", program: "p9", status: "queued" },          // paused program: not counted
    ]);
    expect(c).toEqual({ active: 1, running: 1, awaitingYou: 1, waiting: 2, cantStart: 1 });
  });
});

describe("diskPulse", () => {
  const hosts = [
    { name: "local", label: "this machine", free_gb: 263.1, disk: "" },
    { name: "bmtr", free_gb: 144.4, disk: "" },
    { name: "supersk", free_gb: 92.4, disk: "" },
  ];

  it("says nothing while every machine has room", () => {
    // The pulse is for what needs attention; a healthy pool's figures live on Compute.
    expect(diskPulse(hosts)).toBeNull();
  });

  it("names every machine that reported, on hover, tightest first", () => {
    const d = diskPulse([...hosts, { name: "gpu1", free_gb: 1.4, disk: "low" }])!;
    expect(d.title)
      .toBe("gpu1: 1.4 GB free\nsupersk: 92 GB free\nbmtr: 144 GB free\nthis machine: 263 GB free");
  });

  it("turns to a warning when a machine is running low", () => {
    const d = diskPulse([...hosts, { name: "gpu1", free_gb: 1.4, disk: "low" }])!;
    expect([d.mark, d.text]).toEqual(["⚠", "gpu1 low on disk"]);
  });

  it("turns to a stop when a machine is out of space, and outranks a merely low one", () => {
    const d = diskPulse([{ name: "a", free_gb: 1.4, disk: "low" },
                         { name: "b", free_gb: 0.3, disk: "critical" }])!;
    expect([d.mark, d.color]).toEqual(["⛔", "var(--st-failed)"]);
    expect(d.text).toBe("b out of space · 2 machines");
  });

  it("says nothing at all when nothing has reported", () => {
    expect(diskPulse([])).toBeNull();
    expect(diskPulse([{ name: "a", free_gb: null, disk: "" }])).toBeNull();
  });

  it("ignores a machine that reported nothing rather than treating it as empty", () => {
    const d = diskPulse([{ name: "quiet", free_gb: null, disk: "" },
                         { name: "b", free_gb: 0.3, disk: "critical" }])!;
    expect(d.text).toBe("b out of space");      // not "· 2 machines", and not "quiet"
    expect(d.title).toBe("b: 307 MB free");
  });
});
