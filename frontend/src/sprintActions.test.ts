import { describe, expect, it } from "vitest";
import { availableActions, editableFields } from "./sprintActions";

describe("availableActions", () => {
  it("offers approve + run + edit/reject/demote/park for proposed", () => {
    expect(availableActions("proposed").sort()).toEqual(["approve", "demote", "edit", "park", "reject", "run"]);
  });
  it("offers unpark/demote/cancel for parked", () => {
    expect(availableActions("parked").sort()).toEqual(["cancel", "demote", "unpark"]);
  });
  it("offers run + send back for approved", () => {
    expect(availableActions("approved").sort()).toEqual(["demote", "edit", "reject", "run", "sendBack"]);
  });
  it("offers cancel + edit for queued, only edit for executing", () => {
    expect(availableActions("queued").sort()).toEqual(["edit", "reject"]);
    expect(availableActions("executing")).toEqual(["edit"]);
  });
  it("offers resume for done/failed, nothing for canceled", () => {
    expect(availableActions("done")).toEqual(["resume"]);
    expect(availableActions("failed")).toEqual(["resume"]);
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
