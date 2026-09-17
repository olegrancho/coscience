import { describe, it, expect } from "vitest";
import {
  accessFromHost, accessPayload, accessInvalid, sameAccess, accessLabel, hostAllows,
  onlyProgram, cutOffMessage,
} from "./programAccess";

const onlyThese = { programs: ["p1", "p2"], exclude_programs: [] };
const allExcept = { programs: [], exclude_programs: ["p4"] };
const allHost = { programs: [], exclude_programs: [] };

describe("accessFromHost", () => {
  it("reads an 'only these' host", () => {
    expect(accessFromHost(onlyThese)).toEqual({ all: false, list: ["p1", "p2"] });
  });
  it("reads an 'all except' host", () => {
    expect(accessFromHost(allExcept)).toEqual({ all: true, list: ["p4"] });
  });
  it("reads an 'all' host", () => {
    expect(accessFromHost(allHost)).toEqual({ all: true, list: [] });
  });
  it("defaults to all/empty when there is no host", () => {
    expect(accessFromHost(undefined)).toEqual({ all: true, list: [] });
  });
});

describe("accessPayload", () => {
  it("round trips an 'only these' access", () => {
    const a = { all: false, list: ["p1", "p2"] };
    expect(accessPayload(a)).toEqual({ programs: ["p1", "p2"], exclude_programs: [] });
    expect(accessFromHost(accessPayload(a))).toEqual(a);
  });
  it("round trips an 'all except' access", () => {
    const a = { all: true, list: ["p4"] };
    expect(accessPayload(a)).toEqual({ programs: [], exclude_programs: ["p4"] });
    expect(accessFromHost(accessPayload(a))).toEqual(a);
  });
});

describe("accessInvalid", () => {
  it("flags 'only these' with nothing picked", () => {
    expect(accessInvalid({ all: false, list: [] })).toBe(true);
  });
  it("allows 'only these' with a pick", () => {
    expect(accessInvalid({ all: false, list: ["p1"] })).toBe(false);
  });
  it("allows 'all' with an empty exception list", () => {
    expect(accessInvalid({ all: true, list: [] })).toBe(false);
  });
});

describe("sameAccess", () => {
  it("ignores list order", () => {
    expect(sameAccess({ all: false, list: ["p1", "p2"] }, { all: false, list: ["p2", "p1"] })).toBe(true);
  });
  it("differs on all", () => {
    expect(sameAccess({ all: true, list: [] }, { all: false, list: [] })).toBe(false);
  });
  it("differs on list contents", () => {
    expect(sameAccess({ all: true, list: ["p1"] }, { all: true, list: ["p2"] })).toBe(false);
  });
});

describe("accessLabel", () => {
  it("lists the allowed programs", () => {
    expect(accessLabel(onlyThese)).toBe("p1, p2");
  });
  it("names the exceptions", () => {
    expect(accessLabel(allExcept)).toBe("all except p4");
  });
  it("reads 'all' with no exceptions", () => {
    expect(accessLabel(allHost)).toBe("all");
  });
});

describe("hostAllows", () => {
  it("checks membership for 'only these'", () => {
    expect(hostAllows(onlyThese, "p1")).toBe(true);
    expect(hostAllows(onlyThese, "p3")).toBe(false);
  });
  it("checks exclusion for 'all except'", () => {
    expect(hostAllows(allExcept, "p4")).toBe(false);
    expect(hostAllows(allExcept, "p1")).toBe(true);
  });
  it("allows everything for 'all'", () => {
    expect(hostAllows(allHost, "p1")).toBe(true);
  });
});

describe("onlyProgram", () => {
  it("is true when it is the single allowed program", () => {
    expect(onlyProgram({ programs: ["p1"] }, "p1")).toBe(true);
  });
  it("is false with more than one program", () => {
    expect(onlyProgram({ programs: ["p1", "p2"] }, "p1")).toBe(false);
  });
  it("is false for a different program", () => {
    expect(onlyProgram({ programs: ["p1"] }, "p2")).toBe(false);
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
