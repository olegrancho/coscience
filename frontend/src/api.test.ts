import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { MODEL_OPTIONS } from "./components/ui";

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

  it("createChat sends artifacts when bound", async () => {
    const f = mockFetch(201, { id: "c1", artifacts: ["doc"] });
    await api.createChat("p", "edit", ["doc"]);
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/programs/p/chats");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ title: "edit", artifacts: ["doc"] });
  });

  it("saveChatVersion POSTs to the save route", async () => {
    const f = mockFetch(200, { doc: "v1" });
    const r = await api.saveChatVersion("p", "c1");
    expect(f).toHaveBeenCalledWith("/api/programs/p/chats/c1/save", expect.objectContaining({ method: "POST" }));
    expect(r).toEqual({ doc: "v1" });
  });

  it("work list + read hit the right paths", async () => {
    const f = mockFetch(200, ["content.md"]);
    await api.listArtifactWorkFiles("p", "doc");
    expect(f).toHaveBeenCalledWith("/api/programs/p/artifacts/doc/work");
  });

  it("setProgramMaxProposed posts the cap to the program's max_proposed endpoint", async () => {
    const fetchMock = mockFetch(200, { id: "p1", max_proposed: 6 });

    await expect(api.setProgramMaxProposed("p1", 6)).resolves.toEqual({ id: "p1", max_proposed: 6 });
    expect(fetchMock).toHaveBeenCalledWith("/api/programs/p1/max_proposed", expect.objectContaining({
      method: "POST", body: JSON.stringify({ n: 6 }),
    }));
  });
});

describe("MODEL_OPTIONS", () => {
  it("offers Opus 4.8", () => {
    expect(MODEL_OPTIONS).toContainEqual({ value: "claude-opus-4-8", label: "Opus 4.8" });
  });

  it("has no duplicate values", () => {
    const values = MODEL_OPTIONS.map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
  });
});

describe("wiki api", () => {
  it("fetches a summary", async () => {
    const f = mockFetch(200, { counts: { Concept: 1 } });
    const out = await api.getWikiSummary("p1");
    expect(f).toHaveBeenCalledWith("/api/programs/p1/wiki");
    expect(out.counts.Concept).toBe(1);
  });

  it("keeps the slash in a page slug so the path route matches", async () => {
    const f = mockFetch(200, { path: "concepts/a.md" });
    await api.getWikiPage("p1", "concepts/a");
    expect(f).toHaveBeenCalledWith("/api/programs/p1/wiki/pages/concepts/a");
  });

  it("encodes each slug segment without eating the separator", async () => {
    const f = mockFetch(200, {});
    await api.getWikiPage("p1", "concepts/a b");
    expect(f).toHaveBeenCalledWith("/api/programs/p1/wiki/pages/concepts/a%20b");
  });

  it("posts a status change to the status route", async () => {
    const f = mockFetch(200, { status: "stable" });
    await api.setWikiPageStatus("p1", "concepts/a", "stable");
    expect(f.mock.calls[0][0]).toBe("/api/programs/p1/wiki/status/concepts/a");
    expect(f.mock.calls[0][1]).toMatchObject({ method: "POST" });
  });

  it("encodes the search query", async () => {
    const f = mockFetch(200, []);
    await api.searchWiki("p1", "compute lease");
    expect(f).toHaveBeenCalledWith("/api/programs/p1/wiki/search?q=compute%20lease");
  });
});

describe("wiki merge client", () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({}),
    }) as never;
  });

  it("lists proposals for a program", async () => {
    await api.listWikiMerges("p1");
    expect(global.fetch).toHaveBeenCalledWith("/api/programs/p1/wiki/merges");
  });

  it("accepts and rejects by proposal id", async () => {
    await api.acceptWikiMerge("p1", "m0001");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/programs/p1/wiki/merges/m0001/accept", { method: "POST" });
    await api.rejectWikiMerge("p1", "m0001");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/programs/p1/wiki/merges/m0001/reject", { method: "POST" });
  });

  it("fetches the activity trail", async () => {
    await api.getWikiActivity("p1");
    expect(global.fetch).toHaveBeenCalledWith("/api/programs/p1/wiki/activity");
  });

  it("sends the merge policy as a body, not a query", async () => {
    await api.setWikiMergePolicy("p1", "propose");
    const calls = (global.fetch as never as ReturnType<typeof vi.fn>).mock.calls;
    const [url, init] = calls[calls.length - 1];
    expect(url).toBe("/api/programs/p1/wiki-merge-policy");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ policy: "propose" });
  });
});
