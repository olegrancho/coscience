import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WikiView from "./WikiView";
import { api } from "../api";

// Mantine reads matchMedia and ResizeObserver; jsdom has neither. Same stubs as
// ProgramDetail.test.tsx — without them the provider throws on mount.
beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as never;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as never);
});

const summary = {
  counts: { Concept: 2, Entity: 1 },
  trust: { unverified: 2, "machine-confirmed": 0, "human-reviewed": 1 },
  pages: 3, pending: 4, quarantined: [] as string[], run: null,
  last_run: { id: "r0001", kind: "ingest", status: "ok", at: 1, pages_created: 18,
              pages_updated: 3, notes: "", escaped: [] },
  ingests_since_lint: 1, lint: { error: 0, warn: 2 }, index_md: "# Index",
};

const rows = [
  { path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha", status: "stable",
    trust: "human-reviewed", stale_after: "", tags: [] },
  { path: "concepts/b.md", slug: "b", type: "Concept", title: "Beta", status: "draft",
    trust: "unverified", stale_after: "2020-01-01", tags: [] },
  { path: "entities/c.md", slug: "c", type: "Entity", title: "Gamma", status: "",
    trust: "unverified", stale_after: "", tags: [] },
];

function mount(path = "/programs/p1/wiki") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/programs/:id/wiki" element={<WikiView />} />
          <Route path="/programs/:id/wiki/*" element={<WikiView />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>,
  );
}

describe("WikiView header and tree", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
  });

  it("shows counts, pending and the last run outcome", async () => {
    mount();
    expect(await screen.findByText(/4 pending/i)).toBeTruthy();
    expect(screen.getByText(/Concept 2/)).toBeTruthy();
    expect(screen.getByText(/ingest ok/i)).toBeTruthy();
  });

  it("groups the tree by page type", async () => {
    mount();
    expect(await screen.findByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Gamma")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Entities/i })).toBeTruthy();
  });

  it("marks a stale page", async () => {
    mount();
    await screen.findByText("Beta");
    expect(screen.getByTitle(/stale/i)).toBeTruthy();
  });

  it("triggers an ingest run from the header button", async () => {
    const run = vi.spyOn(api, "runWiki").mockResolvedValue({ line: "ok" } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /ingest now/i }));
    await waitFor(() => expect(run).toHaveBeenCalledWith("p1", "ingest"));
  });

  it("offers a retry only when something is quarantined", async () => {
    mount();
    await screen.findByText("Alpha");
    expect(screen.queryByRole("button", { name: /retry quarantined/i })).toBeNull();
  });

  it("offers the retry when the summary reports quarantined objects", async () => {
    vi.spyOn(api, "getWikiSummary")
      .mockResolvedValue({ ...summary, quarantined: ["result:r9"] } as never);
    mount();
    expect(await screen.findByRole("button", { name: /retry quarantined/i })).toBeTruthy();
  });

  it("searches when the box has a query", async () => {
    const search = vi.spyOn(api, "searchWiki").mockResolvedValue(
      [{ path: "concepts/a.md", title: "Alpha", type: "Concept",
         trust: "unverified", score: 3, excerpt: "…lease…" }] as never);
    mount();
    fireEvent.change(await screen.findByPlaceholderText(/search/i),
                     { target: { value: "lease" } });
    await waitFor(() => expect(search).toHaveBeenCalledWith("p1", "lease"));
  });
});

// Link text and relation titles below deliberately avoid the tree's titles
// (Alpha/Beta/Gamma): the tree renders alongside the page, so a duplicate name
// would make getByRole("link") ambiguous and fail on wiring, not on behaviour.
const centrePage = {
  path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha",
  status: "stable", trust: "human-reviewed", stale_after: "", tags: ["auth"],
  description: "d", aliases: [], human_notes: "", verified: [],
  body: "# Definition\n\nSee [Bee](/concepts/b.md) and [out](https://example.org).\n",
  relations: [], backlinks: [],
  sources: [
    { id: "c1", kind: "result", href: "/results/r7", resource: "/results/r7.md",
      title: "R7" },
    { id: "c2", kind: "artifact", href: "/programs/p1/artifacts/fig",
      resource: "/programs/p1/artifacts/fig/v1", title: "fig" },
    { id: "c3", kind: "unknown", href: "", resource: "https://x.test", title: "loose" },
  ],
};

describe("WikiView centre pane", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiPage").mockResolvedValue(centrePage as never);
  });

  it("renders the page title and body", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect(await screen.findByRole("heading", { name: "Alpha" })).toBeTruthy();
    // By role, not by text: the right pane's outline also renders the word
    // "Definition" as a link, so a bare text query matches two elements.
    expect(screen.getByRole("heading", { name: "Definition" })).toBeTruthy();
  });

  it("rewrites an internal body link to a client-side wiki route", async () => {
    mount("/programs/p1/wiki/concepts/a");
    const link = await screen.findByRole("link", { name: "Bee" });
    expect(link.getAttribute("href")).toBe("/programs/p1/wiki/concepts/b");
  });

  it("leaves an external body link pointing out", async () => {
    mount("/programs/p1/wiki/concepts/a");
    const link = await screen.findByRole("link", { name: "out" });
    expect(link.getAttribute("href")).toBe("https://example.org");
  });

  it("renders provenance chips that link back to the platform", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect((await screen.findByRole("link", { name: /R7/ })).getAttribute("href"))
      .toBe("/results/r7");
    expect(screen.getByRole("link", { name: /fig/ }).getAttribute("href"))
      .toBe("/programs/p1/artifacts/fig");
  });

  it("shows an unroutable source as text rather than a dead link", async () => {
    mount("/programs/p1/wiki/concepts/a");
    await screen.findByRole("heading", { name: "Alpha" });
    expect(screen.queryByRole("link", { name: /loose/ })).toBeNull();
    expect(screen.getByText(/c3: loose/)).toBeTruthy();
  });

  it("falls back to the index when no page is selected", async () => {
    mount("/programs/p1/wiki");
    expect(await screen.findByText("Index")).toBeTruthy();
  });
});

const sidePage = {
  ...centrePage, status: "draft", trust: "unverified", tags: [], human_notes: "old note",
  body: "# Definition\n\nd\n\n# Evidence\n\ne\n",
  sources: [],
  relations: [
    { type: "part_of", target: "concepts/b.md", title: "Bee", exists: true,
      confidence: "high", source: "c1" },
    { type: "requires", target: "concepts/gone.md", title: "", exists: false,
      confidence: "", source: "c1" },
  ],
  backlinks: [{ path: "concepts/z.md", title: "Zeta", type: "Concept",
                typed: ["refines"] }],
};

describe("WikiView right pane", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiPage").mockResolvedValue(sidePage as never);
  });

  it("lists the body outline", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect(await screen.findByRole("link", { name: "Evidence" })).toBeTruthy();
  });

  it("shows backlinks with the relation types pointing here", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect(await screen.findByRole("link", { name: "Zeta" })).toBeTruthy();
    expect(screen.getByText(/refines/)).toBeTruthy();
  });

  it("marks a relation whose target does not exist", async () => {
    mount("/programs/p1/wiki/concepts/a");
    await screen.findByText(/part_of/);
    expect(screen.getByTitle(/missing/i)).toBeTruthy();
  });

  it("marks the page verified", async () => {
    const verify = vi.spyOn(api, "verifyWikiPage")
      .mockResolvedValue({ ...sidePage, trust: "human-reviewed" } as never);
    mount("/programs/p1/wiki/concepts/a");
    fireEvent.click(await screen.findByRole("button", { name: /mark verified/i }));
    await waitFor(() => expect(verify).toHaveBeenCalledWith("p1", "concepts/a"));
  });

  it("changes the lifecycle status", async () => {
    const setStatus = vi.spyOn(api, "setWikiPageStatus")
      .mockResolvedValue({ ...sidePage, status: "stable" } as never);
    mount("/programs/p1/wiki/concepts/a");
    fireEvent.change(await screen.findByLabelText(/status/i),
                     { target: { value: "stable" } });
    await waitFor(() =>
      expect(setStatus).toHaveBeenCalledWith("p1", "concepts/a", "stable"));
  });

  it("saves the human notes", async () => {
    const save = vi.spyOn(api, "setWikiHumanNotes")
      .mockResolvedValue({ ...sidePage, human_notes: "new note" } as never);
    mount("/programs/p1/wiki/concepts/a");
    const box = await screen.findByLabelText(/human notes/i);
    fireEvent.change(box, { target: { value: "new note" } });
    fireEvent.click(screen.getByRole("button", { name: /save notes/i }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("p1", "concepts/a", "new note"));
  });
});
