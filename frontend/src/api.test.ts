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
});
