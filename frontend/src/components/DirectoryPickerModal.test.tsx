import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: { listDirs: vi.fn(), createDir: vi.fn() },
}));

import { api } from "../api";
import DirectoryPickerModal from "./DirectoryPickerModal";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
  // Mantine's ScrollArea.Autosize uses ResizeObserver, absent in jsdom (see ChatView.test.tsx).
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as unknown as typeof ResizeObserver);
});

const listing = (path: string, entries: string[], parent: string | null = null) => ({
  path,
  parent,
  roots: [{ label: "~", path: "/home/oleg" }],
  entries: entries.map((name) => ({ name, path: `${path}/${name}` })),
});

beforeEach(() => {
  vi.mocked(api.listDirs).mockReset();
  vi.mocked(api.createDir).mockReset();
  vi.mocked(api.listDirs).mockImplementation(async (p?: string | null) =>
    p === "/home/oleg/sync"
      ? listing("/home/oleg/sync", ["bmt-share"], "/home/oleg")
      : listing("/home/oleg", ["sync"]));
});

function renderPicker(onPick = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <DirectoryPickerModal opened initialPath="/home/oleg" onClose={() => {}} onPick={onPick} />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return onPick;
}

describe("DirectoryPickerModal", () => {
  it("lists the folders returned for the current path", async () => {
    renderPicker();
    await waitFor(() => expect(screen.getByRole("button", { name: /sync/ })).toBeTruthy());
  });

  it("descends into a folder when it is clicked", async () => {
    renderPicker();
    fireEvent.click(await screen.findByRole("button", { name: /sync/ }));
    await waitFor(() => expect(api.listDirs).toHaveBeenCalledWith("/home/oleg/sync"));
    await waitFor(() => expect(screen.getByRole("button", { name: /bmt-share/ })).toBeTruthy());
  });

  it("calls onPick with the current path when confirmed", async () => {
    const onPick = renderPicker();
    await screen.findByRole("button", { name: /sync/ });
    fireEvent.click(screen.getByRole("button", { name: /use this folder/i }));
    expect(onPick).toHaveBeenCalledWith("/home/oleg");
  });

  it("creates a folder under the current path", async () => {
    vi.mocked(api.createDir).mockResolvedValue({ path: "/home/oleg/fresh" });
    renderPicker();
    await screen.findByRole("button", { name: /sync/ });
    fireEvent.click(screen.getByRole("button", { name: /new folder/i }));
    fireEvent.change(screen.getByLabelText("new folder name"), { target: { value: "fresh" } });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() => expect(api.createDir).toHaveBeenCalledWith("/home/oleg", "fresh"));
  });
});
