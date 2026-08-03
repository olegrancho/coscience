import { describe, expect, it } from "vitest";
import { buildArtifactTree } from "./artifactTree";
import type { ArtifactVersionT } from "../api";

const v = (id: string, parent = ""): ArtifactVersionT =>
  ({ id, parent, created_at: 0, created_by: "", archived: false, note: "" });

describe("buildArtifactTree", () => {
  it("empty list -> empty", () => {
    expect(buildArtifactTree([], "")).toEqual([]);
  });

  it("linear chain stays flush left — no fork, no indent", () => {
    const rows = buildArtifactTree([v("v1"), v("v2", "v1"), v("v3", "v2")], "v3");
    expect(rows.map((r) => r.v.id)).toEqual(["v1", "v2", "v3"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 0, 0]);
    expect(rows.every((r) => r.onCurrentPath)).toBe(true);
  });

  it("indents at the fork, and the branches keep their own indent afterwards", () => {
    // v1 -> v2 -> v3 ; v1 -> v4 (fork at v1) ; v4 -> v5 (only child)
    const rows = buildArtifactTree(
      [v("v1"), v("v2", "v1"), v("v3", "v2"), v("v4", "v1"), v("v5", "v4")], "v3");
    const depth = Object.fromEntries(rows.map((r) => [r.v.id, r.depth]));
    expect(depth).toEqual({ v1: 0, v2: 1, v3: 1, v4: 1, v5: 1 });
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
