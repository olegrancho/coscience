import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ProgramLinks from "./ProgramLinks";
import Md from "./Md";
import { api } from "../api";

describe("idea links on a program's pages", () => {
  it("send a live idea to its row, and a promoted one to its sprint (K1, K4)", async () => {
    vi.spyOn(api, "listIdeas").mockResolvedValue({
      summary: "", ideas: [{ id: "3301b981", text: "t" }],
      promoted: { fc4f2825: "p-s7-run-context" },
    } as any);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}><MemoryRouter>
        <ProgramLinks programId="p"><Md>{"pool 3301b981; promoted fc4f2825"}</Md></ProgramLinks>
      </MemoryRouter></QueryClientProvider>);
    expect((await screen.findByText("fc4f2825")).closest("a")?.getAttribute("href"))
      .toBe("/sprints/p-s7-run-context");
    expect(screen.getByText("3301b981").closest("a")?.getAttribute("href"))
      .toBe("/programs/p/ideas#3301b981");
  });
});
