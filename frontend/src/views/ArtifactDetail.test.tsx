import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ArtifactDetail from "./ArtifactDetail";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
});

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <MemoryRouter initialEntries={["/programs/p/artifacts/doc"]}>
          <Routes><Route path="/programs/:id/artifacts/:aid" element={<ArtifactDetail />} /></Routes>
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>);
}

describe("ArtifactDetail", () => {
  it("renders the title and the version tree", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Manuscript", kind: "md", current: "v2",
      archived: false, lock: {}, current_files: ["content.md"], linked_sprints: [],
      threads: [],
      versions: [
        { id: "v1", parent: "", created_at: 1, created_by: "human", archived: false, note: "first" },
        { id: "v2", parent: "v1", created_at: 2, created_by: "chat:x", archived: false, note: "" },
      ],
    } as any);
    vi.spyOn(api, "readArtifactFile").mockResolvedValue({ name: "content.md", size: 5, content: "hello", binary: false } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("Manuscript")).toBeTruthy());
    expect(screen.getByText("v1")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
  });

  it("shows the lock/owner banner when held", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: { holder_kind: "sprint", holder_id: "s1" }, current_files: [], linked_sprints: [],
      threads: [], versions: [],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText(/s1/)).toBeTruthy());
  });
});
