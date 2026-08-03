import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ProgramDetail from "./ProgramDetail";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as any);
});

function mockProgram(instructions: string) {
  vi.spyOn(api, "getProgram").mockResolvedValue({
    id: "p", title: "P", status: "active", goals: "g", report: "", cycle: 0,
    sprints: [], pm_model: "", workdir: "", activations: [], last_run: null,
    instructions,
  } as any);
  vi.spyOn(api, "listGuidance").mockResolvedValue([]);
  vi.spyOn(api, "listIdeas").mockResolvedValue({ summary: "", ideas: [] } as any);
  vi.spyOn(api, "listArtifacts").mockResolvedValue([]);
}

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p"]}>
        <Routes><Route path="/programs/:id" element={<ProgramDetail />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
}

describe("general instructions", () => {
  it("shows the stored instructions", async () => {
    mockProgram("Cite a source for every claim.");
    renderAt();
    await waitFor(() => expect(screen.getByText("Cite a source for every claim.")).toBeTruthy());
  });

  it("edits and saves them", async () => {
    mockProgram("");
    const save = vi.spyOn(api, "setProgramInstructions").mockResolvedValue({} as any);
    renderAt();
    // Unset, the card still says so — otherwise nobody would know it exists.
    await waitFor(() => expect(screen.getByText(/works from the goals/i)).toBeTruthy());

    // By title, not text: the guidance card below has an "Add" button too.
    fireEvent.click(screen.getByTitle("Add general instructions"));
    const box = await screen.findByPlaceholderText(/cite a source/i);
    fireEvent.change(box, { target: { value: "Be terse." } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(save).toHaveBeenCalledWith("p", "Be terse."));
  });
});
