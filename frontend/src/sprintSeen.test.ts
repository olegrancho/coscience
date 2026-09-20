import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { isUnseen, markSeen, seedIfNew } from "./sprintSeen";

const T0 = 1_700_000_000;          // the moment the viewer last looked
const MOVED = T0 + 60;             // the platform moves the sprint a minute later
const EARLIER = T0 - 60;           // it was last moved before the viewer ever looked

/** The clock is faked so "seen" and "changed" can be ordered deliberately: these
 *  are comparisons between a stored wall-clock time and a status timestamp. */
beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(T0 * 1000);
});
afterEach(() => vi.useRealTimers());

/** The sprint is on the board of a program the viewer has opened before, and they
 *  have seen it as it was. */
function onTheBoard(id: string) {
  seedIfNew([{ id }], "p1");
}

describe("isUnseen", () => {
  it("highlights a sprint the PM moved after the viewer last looked", () => {
    onTheBoard("s1");
    expect(isUnseen("s1", MOVED, "pm")).toBe(true);
  });

  it("highlights a sprint the platform moved — a worker finishing or failing", () => {
    onTheBoard("s1");
    expect(isUnseen("s1", MOVED, "platform")).toBe(true);
  });

  it("does NOT highlight a change the human just made themselves", () => {
    // Approving or parking an experiment used to make the row announce the click
    // back at whoever clicked it, which is what this whole flag exists to stop.
    onTheBoard("s1");
    expect(isUnseen("s1", MOVED, "human")).toBe(false);
  });

  it("treats a missing actor as the platform, the way it behaved before the field", () => {
    onTheBoard("s1");
    expect(isUnseen("s1", MOVED, undefined)).toBe(true);
  });

  it("still ignores a sprint with no recorded status time", () => {
    onTheBoard("s1");
    expect(isUnseen("s1", null, "pm")).toBe(false);
  });

  it("highlights a sprint that appeared since the viewer last looked", () => {
    // A newly proposed sprint arrives with no "seen" record at all — that IS the
    // signal. Before this it was seeded as already-seen and never lit up, so the
    // planner could propose all night and the board looked untouched.
    onTheBoard("s1");
    expect(isUnseen("brand-new", MOVED, "pm")).toBe(true);
  });

  it("does not highlight a sprint the viewer proposed themselves", () => {
    onTheBoard("s1");
    expect(isUnseen("brand-new", MOVED, "human")).toBe(false);
  });

  it("clears once the viewer opens the sprint", () => {
    onTheBoard("s1");
    expect(isUnseen("s1", MOVED, "pm")).toBe(true);
    vi.setSystemTime((MOVED + 60) * 1000);
    markSeen("s1");
    expect(isUnseen("s1", MOVED, "pm")).toBe(false);
  });

  it("does not highlight a platform change older than the last view", () => {
    markSeen("s1");
    expect(isUnseen("s1", T0 - 60, "platform")).toBe(false);
  });
});

describe("seedIfNew", () => {
  it("keeps a program's first visit quiet", () => {
    // Opening a program with 33 sprints for the first time must not arrive as 33
    // highlights — that is the whole job of the seed.
    const board = [{ id: "a" }, { id: "b" }, { id: "c" }];
    seedIfNew(board, "p1");
    for (const s of board) expect(isUnseen(s.id, EARLIER, "pm")).toBe(false);
  });

  it("lets a sprint that arrives AFTER that first visit highlight", () => {
    seedIfNew([{ id: "a" }], "p1");
    seedIfNew([{ id: "a" }, { id: "new" }], "p1");   // a later poll brings a new one
    expect(isUnseen("new", MOVED, "pm")).toBe(true);
  });

  it("keeps each program's first visit separate", () => {
    seedIfNew([{ id: "p1-a" }], "p1");
    // p2 has never been opened, so ITS board is seeded quiet too.
    seedIfNew([{ id: "p2-a" }], "p2");
    expect(isUnseen("p2-a", EARLIER, "pm")).toBe(false);
  });

  it("seeds nothing without a program id, so an unknown sprint still highlights", () => {
    seedIfNew([{ id: "a" }]);
    expect(isUnseen("a", EARLIER, "pm")).toBe(false);   // it was seeded
    expect(isUnseen("b", MOVED, "pm")).toBe(true);    // it was not
  });
});
