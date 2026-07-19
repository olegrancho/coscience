import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body),
                 { status, headers: { "Content-Type": "application/json" } }) as Response);
}

describe("api client", () => {
  it("getProgram hits the prefixed path and parses JSON", async () => {
    const f = mockFetch(200, { id: "p1", title: "t", status: "active", goals: "g",
                               report: "r", cycle: 1, sprints: [] });
    const p = await api.getProgram("p1");
    expect(f).toHaveBeenCalledWith("/api/programs/p1");
    expect(p.cycle).toBe(1);
  });

  it("editSprint sends a PATCH with the patch body", async () => {
    const f = mockFetch(200, { id: "sp1" });
    await api.editSprint("sp1", { priority: 5 });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/sprints/sp1");
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ priority: 5 });
  });

  it("throws on a non-ok response", async () => {
    mockFetch(404, { detail: "nope" });
    await expect(api.getSprint("x")).rejects.toThrow("404");
  });

  it("getArtifact hits the prefixed path", async () => {
    const f = mockFetch(200, { id: "doc", program: "p", title: "Doc", kind: "md",
      current: "v1", archived: false, lock: {}, versions: [], threads: [],
      current_files: ["content.md"], linked_sprints: [] });
    const d = await api.getArtifact("p", "doc");
    expect(f).toHaveBeenCalledWith("/api/programs/p/artifacts/doc");
    expect(d.current).toBe("v1");
  });

  it("revertArtifact POSTs the vid", async () => {
    const f = mockFetch(200, { id: "doc", current: "v1" });
    await api.revertArtifact("p", "doc", "v1");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/programs/p/artifacts/doc/revert");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ vid: "v1" });
  });

  it("addArtifactComment POSTs text + thread_id", async () => {
    const f = mockFetch(201, { id: "t1" });
    await api.addArtifactComment("p", "doc", "tighten intro");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/programs/p/artifacts/doc/comments");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ text: "tighten intro", thread_id: "" });
  });

  it("download/page url helpers build the right paths", () => {
    expect(api.artifactDownloadUrl("p", "doc", "v2")).toBe("/api/programs/p/artifacts/doc/versions/v2/download");
    expect(api.artifactPageUrl("p", "site", "v1", "index.html")).toBe("/api/programs/p/artifacts/site/versions/v1/page/index.html");
  });

  it("submitSprint forwards artifacts_bound/create", async () => {
    const f = mockFetch(201, { id: "s1" });
    await api.submitSprint({ id: "s1", goals: "g", plan: ["x"], program: "p",
      artifacts_bound: ["doc"], artifacts_create: [{ aid: "fig", title: "Fig", kind: "figure" }] });
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(body.artifacts_bound).toEqual(["doc"]);
    expect(body.artifacts_create).toEqual([{ aid: "fig", title: "Fig", kind: "figure" }]);
  });
});
