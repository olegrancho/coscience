import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ProgramsOverview from "./ProgramsOverview";
import { api } from "../api";

const PROGRAMS = [
  { id: "p2", title: "Lead Finder", status: "active", goals: "g" },
  { id: "p1", title: "Demo", status: "paused", goals: "g" },
  { id: "p5", title: "GCN", status: "active", goals: "g" },
  { id: "wikitest", title: "Wiki test", status: "closed", goals: "g" },
];

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  vi.spyOn(api, "listPrograms").mockResolvedValue(PROGRAMS as any);
  vi.spyOn(api, "listSprints").mockResolvedValue([] as any);
});

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter><ProgramsOverview /></MemoryRouter>
    </MantineProvider></QueryClientProvider>,
  );
}

describe("ProgramsOverview", () => {
  it("heads each state with its own count, and skips states with nothing in them", async () => {
    renderView();
    expect(await screen.findByText(/active · 2/i)).toBeTruthy();
    expect(screen.getByText(/paused · 1/i)).toBeTruthy();
    expect(screen.queryByText(/archived/i)).toBeNull();
  });

  it("keeps closed programs folded until asked", async () => {
    renderView();
    const toggle = await screen.findByRole("button", { name: /closed · 1/i });
    expect(screen.queryByText("Wiki test")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText("Wiki test")).toBeTruthy();
  });
});
