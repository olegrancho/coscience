import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { isUnseen, markSeen, seedIfNew } from "./sprintSeen";

const T0 = 1_700_000_000;          // the moment the viewer last looked
const MOVED = T0 + 60;             // the platform moves the sprint a minute later

/** The clock is faked so "seen" and "changed" can be ordered deliberately: these
 *  are comparisons between a stored wall-clock time and a status timestamp. */
beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(T0 * 1000);
});
afterEach(() => vi.useRealTimers());

/** The sprint is on the board and the viewer has seen it as it was. */
function onTheBoard(id: string) {
  seedIfNew([{ id }]);
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

  it("still ignores a sprint the viewer has never encountered", () => {
    expect(isUnseen("never-seen", MOVED, "pm")).toBe(false);
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
