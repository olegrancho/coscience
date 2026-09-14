import { describe, it, expect } from "vitest";
import { pulseCounts } from "./pulseCounts";

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
