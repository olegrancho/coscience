import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({
  api: {
    migrateWiki: vi.fn().mockResolvedValue({}),
    cancelWikiMigration: vi.fn().mockResolvedValue({ cancelled: true }),
    setProgramWikiModel: vi.fn(), setWikiMergePolicy: vi.fn(), setProgramWikiEnabled: vi.fn(),
    getWikiDocs: vi.fn().mockResolvedValue({ id: "p1", workdir: "/w", files: [
      { path: "README.md", selected: true, ingested: true },
      { path: "docs/devlog.md", selected: false, ingested: false },
      { path: "OLD.md", selected: true, ingested: true, missing: true }] }),
    setWikiDocs: vi.fn().mockResolvedValue({ id: "p1", workdir: "/w", files: [] }),
  },
}));

import { api, type WikiSummary } from "../api";
import WikiSettingsModal, { migrationText } from "./WikiSettingsModal";

beforeAll(() => {
  window.matchMedia ??= (() => ({ matches: false, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
});

const base = {
  counts: {}, trust: { unverified: 0, "machine-confirmed": 0, "human-reviewed": 0 },
  pages: 3, pending: 0, quarantined: [], run: null, last_run: null, ingests_since_lint: 0,
  lint: {}, wiki_model: "claude-opus-5-5", wiki_enabled: true, wiki_merge: "auto",
  merge_proposals: 0, index_md: "", layout: "concepts v1", layout_current: "topics v1",
  layout_upgrade: "topics v1", migration: null,
} as WikiSummary;

const open = (summary: WikiSummary, onSaved = vi.fn()) => {
  render(
    <MantineProvider><MemoryRouter>
      <WikiSettingsModal opened onClose={() => {}} programId="p1" summary={summary}
                         locked={false} onSaved={onSaved} />
    </MemoryRouter></MantineProvider>);
  return onSaved;
};

describe("WikiSettingsModal layout", () => {
  it("offers the migration when a newer layout exists", async () => {
    const onSaved = open(base);
    expect(screen.getByText("concepts v1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Migrate to topics v1" }));
    await waitFor(() => expect(api.migrateWiki).toHaveBeenCalledWith("p1"));
    expect(onSaved).toHaveBeenCalled();
  });

  it("says current and offers nothing when the wiki is up to date", () => {
    open({ ...base, layout: "topics v1", layout_upgrade: "" });
    expect(screen.getByText(/current/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Migrate/ })).toBeNull();
  });

  it("shows progress, and a resume after a failure", () => {
    open({ ...base, migration: { from: "concepts v1", to: "topics v1", phase: "write",
      batch: 2, batches: 9, failures: 3, error: "write failed 3 times",
      requested_by: "cli", at: 1 } });
    expect(screen.getByText(/batch 3 of 9/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
    expect(screen.getByText(/The old wiki is still live/)).toBeTruthy();
  });

  it("describes every phase", () => {
    const m = { from: "a v1", to: "b v1", batch: 0, batches: 4, failures: 0, error: "",
                requested_by: "", at: 0 };
    expect(migrationText({ ...m, phase: "map" })).toContain("topic map");
    expect(migrationText({ ...m, phase: "finish" })).toContain("index");
  });
});

describe("WikiSettingsModal documentation", () => {
  it("lists the working folder's files with the ticked ones checked", async () => {
    open(base);
    await waitFor(() => expect(screen.getByText("2 of 3 files are wiki sources")).toBeTruthy());
    expect((screen.getByRole("checkbox", { name: /README\.md/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: /devlog/ }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/missing/)).toBeTruthy();
  });

  it("saves the new choice in list order, and only when it changed", async () => {
    open(base);
    await screen.findByText("2 of 3 files are wiki sources");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setProgramWikiModel).not.toHaveBeenCalled());
    expect(api.setWikiDocs).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /devlog/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /OLD\.md/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setWikiDocs).toHaveBeenCalledWith("p1", ["README.md", "docs/devlog.md"]));
  });
});
