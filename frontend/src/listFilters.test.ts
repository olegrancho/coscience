import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, loadFilters, saveFilters } from "./listFilters";

describe("experiments list filters (B2)", () => {
  beforeEach(() => sessionStorage.clear());

  it("start unfiltered", () => {
    expect(loadFilters("p1")).toEqual(DEFAULT_FILTERS);
  });

  it("come back as they were left, per program", () => {
    saveFilters("p1", { statusFilter: "done", onlyNew: true, showAll: true });
    expect(loadFilters("p1")).toEqual({ statusFilter: "done", onlyNew: true, showAll: true });
    expect(loadFilters("p2")).toEqual(DEFAULT_FILTERS);
  });

  it("ignore a damaged record", () => {
    sessionStorage.setItem("coscience:list-filters:p1", "{not json");
    expect(loadFilters("p1")).toEqual(DEFAULT_FILTERS);
  });
});
