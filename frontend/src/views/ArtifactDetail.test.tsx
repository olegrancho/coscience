import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
import ArtifactDetail from "./ArtifactDetail";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
});

/** Stands in for the chat view so a redirect is observable, `?c=` and all. */
function ChatStub() {
  const [sp] = useSearchParams();
  return <div>chat page c={sp.get("c")}</div>;
}

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <MemoryRouter initialEntries={["/programs/p/artifacts/doc"]}>
          <Routes>
            <Route path="/programs/:id/artifacts/:aid" element={<ArtifactDetail />} />
            <Route path="/programs/:id/chat" element={<ChatStub />} />
          </Routes>
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

  it("renders an existing comment thread and the linked sprint", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: {}, current_files: [], versions: [],
      linked_sprints: [{ id: "p-c1-x", status: "queued", title: "Draft" }],
      threads: [{ id: "t1", target: "pm", status: "open", agent_unseen: false, created_at: 1,
                  messages: [{ role: "human", text: "tighten intro", by: "oleg", at: 1 }] }],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("tighten intro")).toBeTruthy());
    expect(screen.getByText(/p-c1-x/)).toBeTruthy();
  });

  it("folds a version's note away until its header is clicked", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "v2",
      archived: false, lock: {}, current_files: [], linked_sprints: [], threads: [],
      versions: [
        { id: "v1", parent: "", created_at: 1, created_by: "human", archived: false, note: "why I changed it" },
        { id: "v2", parent: "v1", created_at: 2, created_by: "human", archived: false, note: "" },
      ],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("v1")).toBeTruthy());
    expect(screen.queryByText("why I changed it")).toBeNull();

    fireEvent.click(screen.getByText("v1"));
    expect(screen.getByText("why I changed it")).toBeTruthy();

    fireEvent.click(screen.getByText("v1"));
    expect(screen.queryByText("why I changed it")).toBeNull();

    // A note-less version has nothing to unfold, so its header doesn't toggle.
    expect(screen.getByText("v2").closest("button")!.hasAttribute("disabled")).toBe(true);
  });

  it("redirects to the chat editing it, and stays put for a sprint lock", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: { holder_kind: "chat", holder_id: "chat:ab12" }, current_files: [],
      linked_sprints: [], threads: [], versions: [],
    } as any);
    const { unmount } = renderAt();
    await waitFor(() => expect(screen.getByText(/chat page c=ab12/)).toBeTruthy());
    unmount();

    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: { holder_kind: "sprint", holder_id: "s1" }, current_files: [],
      linked_sprints: [], threads: [], versions: [],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("Doc")).toBeTruthy());
    expect(screen.queryByText(/chat page/)).toBeNull();
  });

  it("shows an Open chat action", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: {}, current_files: [], linked_sprints: [], threads: [], versions: [],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText(/open chat/i)).toBeTruthy());
  });
});
