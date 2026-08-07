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
  const result = render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p/chat"]}>
        <Routes><Route path="/programs/:id/chat" element={<ChatView />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
  return { ...result, qc };
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

  it("shows a bound figure's live description under the image", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit fig", scope: "full", created_at: 1, busy: false,
        messages: 0, last_at: 1, artifacts: ["fig"] }] as any);
    vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit fig", scope: "full", created_at: 1, turns_done: 0,
      busy: false, messages: [], live: "", artifacts: ["fig"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["description.md", "plot.png"]);
    vi.spyOn(api, "readArtifactWorkFile").mockResolvedValue({
      name: "description.md", size: 9, content: "Log-log gap size.", binary: false } as any);
    renderAt();
    // The image stays the pane's subject even though a text file is now present.
    await waitFor(() => expect(screen.getByAltText("plot.png")).toBeTruthy());
    // The description query only enables once the image is confirmed, so its data
    // lands a render after the image does — wait for it rather than racing it.
    await waitFor(() => expect(screen.getByText("Log-log gap size.")).toBeTruthy());
  });

  it("shows a bound figure's image with no caption section when there's no description.md", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit fig", scope: "full", created_at: 1, busy: false,
        messages: 0, last_at: 1, artifacts: ["fig"] }] as any);
    vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit fig", scope: "full", created_at: 1, turns_done: 0,
      busy: false, messages: [], live: "", artifacts: ["fig"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["plot.png"]);
    const readWorkFile = vi.spyOn(api, "readArtifactWorkFile");
    renderAt();
    await waitFor(() => expect(screen.getByAltText("plot.png")).toBeTruthy());
    // No description.md in the file list -> no caption query, no caption section.
    expect(readWorkFile).not.toHaveBeenCalled();
  });

  it("still renders description.md as the pane's text when there's no image to caption", async () => {
    // A regression guard: description.md must only be treated as a caption when an
    // image is actually present. Without one (e.g. an `md` artifact whose work copy
    // happens to hold description.md + a non-image file), it's the pane's prose —
    // excluding it would make the text unreachable and let the other file win instead.
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit doc", scope: "full", created_at: 1, busy: false,
        messages: 0, last_at: 1, artifacts: ["doc"] }] as any);
    vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit doc", scope: "full", created_at: 1, turns_done: 0,
      busy: false, messages: [], live: "", artifacts: ["doc"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["description.md", "analysis.py"]);
    const readWorkFile = vi.spyOn(api, "readArtifactWorkFile").mockResolvedValue({
      name: "description.md", size: 20, content: "Explains the analysis.", binary: false } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("Explains the analysis.")).toBeTruthy());
    expect(readWorkFile).toHaveBeenCalledWith("p", "doc", "description.md");
  });

  it("refreshes the caption, not just the image, once a busy turn goes idle", async () => {
    // The turn's final edit can land in description.md a moment before the turn ends.
    // Polling for the caption stops the instant busy flips false, so without a
    // matching one-shot invalidation the image updates but the caption is stuck on
    // its pre-edit text until a remount.
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit fig", scope: "full", created_at: 1, busy: true,
        messages: 0, last_at: 1, artifacts: ["fig"] }] as any);
    const getThread = vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit fig", scope: "full", created_at: 1, turns_done: 0,
      busy: true, messages: [], live: "working…", artifacts: ["fig"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["description.md", "plot.png"]);
    vi.spyOn(api, "readArtifactWorkFile")
      .mockResolvedValueOnce({ name: "description.md", size: 9, content: "draft caption.", binary: false } as any)
      .mockResolvedValue({ name: "description.md", size: 9, content: "final caption.", binary: false } as any);

    const { qc } = renderAt();
    await waitFor(() => expect(screen.getByText("draft caption.")).toBeTruthy());

    // The turn ends: the next thread poll would see busy:false. Simulate that poll
    // landing (rather than waiting out the real refetchInterval) by updating the
    // mock and invalidating the thread query the same way the interval's own
    // refetch would.
    getThread.mockResolvedValue({
      id: "c1", title: "edit fig", scope: "full", created_at: 1, turns_done: 1,
      busy: false, messages: [], live: "", artifacts: ["fig"] } as any);
    await qc.invalidateQueries({ queryKey: ["chat", "p", "c1"] });

    await waitFor(() => expect(screen.getByText("final caption.")).toBeTruthy());
  });
});
