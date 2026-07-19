import { describe, expect, it } from "vitest";
import { buildArtifactTree } from "./artifactTree";
import type { ArtifactVersionT } from "../api";

const v = (id: string, parent = ""): ArtifactVersionT =>
  ({ id, parent, created_at: 0, created_by: "", archived: false, note: "" });

describe("buildArtifactTree", () => {
  it("empty list -> empty", () => {
    expect(buildArtifactTree([], "")).toEqual([]);
  });

  it("linear chain has increasing depth, all on current path when current is the leaf", () => {
    const rows = buildArtifactTree([v("v1"), v("v2", "v1"), v("v3", "v2")], "v3");
    expect(rows.map((r) => r.v.id)).toEqual(["v1", "v2", "v3"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2]);
    expect(rows.every((r) => r.onCurrentPath)).toBe(true);
  });

  it("branch: only the ancestors of current are on the path", () => {
    // v1 -> v2 ; v1 -> v3 (branch); current = v2
    const rows = buildArtifactTree([v("v1"), v("v2", "v1"), v("v3", "v1")], "v2");
    const byId = Object.fromEntries(rows.map((r) => [r.v.id, r]));
    expect(byId["v1"].onCurrentPath).toBe(true);
    expect(byId["v2"].onCurrentPath).toBe(true);
    expect(byId["v3"].onCurrentPath).toBe(false);
    expect(byId["v3"].depth).toBe(1);
  });

  it("orphaned parent is treated as a root", () => {
    const rows = buildArtifactTree([v("v2", "gone")], "v2");
    expect(rows.map((r) => r.v.id)).toEqual(["v2"]);
    expect(rows[0].depth).toBe(0);
  });
});
