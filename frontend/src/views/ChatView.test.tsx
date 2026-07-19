import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ChatView from "./ChatView";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
  // Mantine SegmentedControl's FloatingIndicator uses ResizeObserver, absent in jsdom.
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as any);
});

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p/chat"]}>
        <Routes><Route path="/programs/:id/chat" element={<ChatView />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
}

describe("ChatView bound split-view", () => {
  it("shows the Save as version button for a bound chat", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit doc", scope: "full", created_at: 1, busy: false,
        messages: 0, last_at: 1, artifacts: ["doc"] }] as any);
    vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit doc", scope: "full", created_at: 1, turns_done: 0,
      busy: false, messages: [], live: "", artifacts: ["doc"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["content.md"]);
    vi.spyOn(api, "readArtifactWorkFile").mockResolvedValue({ name: "content.md", size: 2, content: "hi", binary: false } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText(/save as version/i)).toBeTruthy());
  });
});
