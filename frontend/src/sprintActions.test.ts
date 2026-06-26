import { describe, expect, it } from "vitest";
import { availableActions, editableFields } from "./sprintActions";

describe("availableActions", () => {
  it("offers approve+reject+edit for proposed", () => {
    expect(availableActions("proposed").sort()).toEqual(["approve", "edit", "reject"]);
  });
  it("offers only edit for approved/executing", () => {
    expect(availableActions("approved")).toEqual(["edit"]);
    expect(availableActions("executing")).toEqual(["edit"]);
  });
  it("offers nothing for done/canceled", () => {
    expect(availableActions("done")).toEqual([]);
    expect(availableActions("canceled")).toEqual([]);
  });
});

describe("editableFields", () => {
  it("allows all fields when proposed", () => {
    expect(editableFields("proposed")).toEqual(
      { goals: true, plan: true, priority: true, resources: true, preemptible: true });
  });
  it("allows only scheduler fields when approved/executing", () => {
    expect(editableFields("executing")).toEqual(
      { goals: false, plan: false, priority: true, resources: true, preemptible: true });
  });
  it("allows nothing when done", () => {
    expect(editableFields("done")).toEqual(
      { goals: false, plan: false, priority: false, resources: false, preemptible: false });
  });
});
