import { describe, it, expect } from "vitest";
import { hostAllows, accessLabel, programsForEdit, cutOffMessage } from "./programAccess";

const listed = { programs: ["p1", "p2"] };
const none = { programs: [] };
const neverSet = { programs: null };

describe("hostAllows", () => {
  it("checks membership when a list is set", () => {
    expect(hostAllows(listed, "p1")).toBe(true);
    expect(hostAllows(listed, "p3")).toBe(false);
  });
  it("allows nothing once the list is set empty", () => {
    expect(hostAllows(none, "p1")).toBe(false);
  });
  it("allows everything when the list has never been set", () => {
    expect(hostAllows(neverSet, "p1")).toBe(true);
  });
});

describe("accessLabel", () => {
  it("lists the programs", () => {
    expect(accessLabel(listed)).toBe("p1, p2");
  });
  it("reads 'none' for an empty list", () => {
    expect(accessLabel(none)).toBe("none");
  });
  it("reads 'all' when never set", () => {
    expect(accessLabel(neverSet)).toBe("all");
  });
});

describe("programsForEdit", () => {
  it("shows the server's own list", () => {
    expect(programsForEdit(listed, ["p1", "p2", "p3"])).toEqual(["p1", "p2"]);
  });
  it("shows every program when the server's list has never been set", () => {
    expect(programsForEdit(neverSet, ["p1", "p2", "p3"])).toEqual(["p1", "p2", "p3"]);
  });
  it("shows nothing ticked when the server's list is set empty", () => {
    expect(programsForEdit(none, ["p1", "p2", "p3"])).toEqual([]);
  });
  it("falls back to every program with no host at all", () => {
    expect(programsForEdit(undefined, ["p1", "p2"])).toEqual(["p1", "p2"]);
  });
});

describe("cutOffMessage", () => {
  it("names 'this machine' for local and the host name otherwise", () => {
    const msg = cutOffMessage([{ sprint_id: "s1", host: "local" }, { sprint_id: "s2", host: "gpu-box" }]);
    expect(msg).toBe(
      "s1 is pinned to this machine; s2 is pinned to gpu-box"
      + ". It keeps its work there and waits until the program is allowed back on that server or the sprint is stopped."
    );
  });
});
