import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WikiNeighbourhood from "./WikiNeighbourhood";
import { api } from "../api";

const graph = {
  nodes: [
    { id: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 0, out_degree: 1, orphan: false, cluster: 0 },
    { id: "concepts/b.md", slug: "b", title: "Beta", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 1, out_degree: 0, orphan: false, cluster: 0 },
  ],
  edges: [{ id: "e", src: "concepts/a.md", dst: "concepts/b.md", type: "refines",
            confidence: "high", source: "s", typed: true, materialized: false }],
};

function show(slug: string, pageType?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WikiNeighbourhood programId="p1" slug={slug} pageType={pageType} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getWikiGraph").mockResolvedValue(graph as never);
});

describe("WikiNeighbourhood", () => {
  it("shows the current page and its neighbours", async () => {
    // "concepts/a" is the page ADDRESS (bundle path minus ".md"), which is
    // what the caller (WikiView) actually passes as `slug` — not the bare
    // filename stem "a". A flat "a" fixture would pass by coincidence even
    // if the centre match compared against the wrong field.
    show("concepts/a");
    expect(await screen.findByTitle("Alpha")).toBeTruthy();
    expect(await screen.findByTitle("Beta")).toBeTruthy();
  });

  it("tells the reader when the page is not in the graph at all, with no pageType given", async () => {
    // No `pageType` is passed here (the "caller didn't look" case), so the
    // generic fallback message is what should show — not the Source-specific
    // one (covered separately below) or the "no such page" one.
    show("sources/result-wt-r1");
    expect(await screen.findByText(/not in the concept graph/i)).toBeTruthy();
  });

  it("tells the reader plainly when no page exists at the address at all", async () => {
    show("nope", "");
    expect(await screen.findByText(/no page was found/i)).toBeTruthy();
  });

  it("reports a load failure distinctly, not as 'not in the concept graph'", async () => {
    vi.spyOn(api, "getWikiGraph").mockRejectedValue(new Error("boom"));
    show("concepts/a");
    expect(await screen.findByText(/could not load/i)).toBeTruthy();
    expect(screen.queryByText(/not in the concept graph/i)).toBeNull();
  });

  it("lists the pages citing a Source page below the explanation", async () => {
    vi.spyOn(api, "getWikiCitations").mockResolvedValue([
      { path: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept" },
    ] as never);
    // The address "sources/result-wt-r1" (not the bare slug "result-wt-r1")
    // is what a real caller passes; oidForSourceSlug must be fed only the
    // last path segment to resolve the right object id.
    show("sources/result-wt-r1", "Source");
    expect(await screen.findByText(/sources are excluded/i)).toBeTruthy();
    expect(await screen.findByRole("link", { name: "Alpha" })).toBeTruthy();
    expect(api.getWikiCitations).toHaveBeenCalledWith("p1", "result:wt-r1");
  });
});

// A hub with four neighbours puts one node on each axis — left, right, top and
// bottom — so every label-placement branch is exercised at once. The titles are
// far longer than fits beside a disc in a 300px rail, so truncation is real.
const cross = {
  nodes: ["hub", "n1", "n2", "n3", "n4"].map((s, i) => ({
    id: `concepts/${s}.md`, slug: s,
    title: `${s === "hub" ? "Hub" : "Neighbour"} ${s} of the ivywrel correlation`,
    type: "Concept", status: "draft", trust: "unverified",
    in_degree: i === 0 ? 4 : 1, out_degree: i === 0 ? 4 : 1, orphan: false, cluster: 0,
  })),
  edges: ["n1", "n2", "n3", "n4"].map((s) => ({
    id: `hub->${s}`, src: "concepts/hub.md", dst: `concepts/${s}.md`,
    type: "refines", confidence: "high", source: "s", typed: true, materialized: false,
  })),
};

const BOX_W = 300, BOX_H = 250, CHAR_PX = 5;

const labelsOf = () => Array.from(document.querySelectorAll("svg text"));

async function showCross() {
  vi.spyOn(api, "getWikiGraph").mockResolvedValue(cross as never);
  show("concepts/hub");
  await screen.findByTitle("Hub hub of the ivywrel correlation");
}

describe("WikiNeighbourhood annotation", () => {
  it("names every node in the picture, not only on hover", async () => {
    show("concepts/a");
    await screen.findByTitle("Alpha");
    const text = labelsOf().map((t) => t.textContent);
    // Two short titles in a 300px box: nothing is cut, so this is exact.
    expect(text).toContain("Alpha");
    expect(text).toContain("Beta");
  });

  it("ellipsises a title that will not fit, and keeps the full one on hover", async () => {
    await showCross();
    const titles = cross.nodes.map((n) => n.title);
    // Nothing is lost — the untruncated title is still on the disc itself.
    for (const t of titles) expect(screen.getByTitle(t)).toBeTruthy();

    const drawn = labelsOf().map((t) => t.textContent ?? "");
    expect(drawn).toHaveLength(5);            // every node is named
    expect(drawn.some((l) => l.endsWith("…"))).toBe(true);   // ...and some are cut
    // Whatever is drawn is a genuine prefix of a real title, never a mismatch.
    for (const l of drawn) {
      expect(titles.some((t) => t.startsWith(l.replace(/…$/, "")))).toBe(true);
    }
  });

  it("draws every disc fully inside the box", async () => {
    // Regression guard: the ring radius used to equal the box's half-width,
    // which put each node's CENTRE on the edge and clipped half of every disc.
    await showCross();
    const discs = Array.from(document.querySelectorAll("svg circle"));
    expect(discs).toHaveLength(5);
    for (const c of discs) {
      const cx = Number(c.getAttribute("cx")), cy = Number(c.getAttribute("cy"));
      const r = Number(c.getAttribute("r"));
      expect(cx - r).toBeGreaterThanOrEqual(0);
      expect(cy - r).toBeGreaterThanOrEqual(0);
      expect(cx + r).toBeLessThanOrEqual(BOX_W);
      expect(cy + r).toBeLessThanOrEqual(BOX_H);
    }
  });

  it("keeps every label inside the box, whichever side of the ring it is on", async () => {
    await showCross();
    const texts = labelsOf();
    expect(texts).toHaveLength(5);
    for (const t of texts) {
      const x = Number(t.getAttribute("x"));
      const w = (t.textContent ?? "").length * CHAR_PX;
      const anchor = t.getAttribute("text-anchor");
      const left = anchor === "start" ? x : anchor === "end" ? x - w : x - w / 2;
      expect(left).toBeGreaterThanOrEqual(0);
      expect(left + w).toBeLessThanOrEqual(BOX_W);
    }
  });

  it("places each label outward, on the side of the ring its node is on", async () => {
    // Outward placement is what keeps a label off the spokes: anchoring a
    // left-hand node's label "start" would draw it back across the picture.
    await showCross();
    const texts = labelsOf();
    const sided = texts.filter((t) => t.getAttribute("text-anchor") !== "middle");
    expect(sided.length).toBe(2);             // the left and right neighbours
    for (const t of sided) {
      const x = Number(t.getAttribute("x"));
      expect(t.getAttribute("text-anchor")).toBe(x > BOX_W / 2 ? "start" : "end");
    }
    // The nodes on the vertical axis go above and below their discs instead —
    // both branches, plus the centre's label under its own disc.
    const upright = texts.filter((t) => t.getAttribute("text-anchor") === "middle");
    expect(upright).toHaveLength(3);
    const dys = upright.map((t) => t.getAttribute("dy"));
    expect(dys).toContain("0");               // the top neighbour, above its disc
    expect(dys.filter((d) => d === "0.8em")).toHaveLength(2);  // bottom + centre
  });
});

describe("WikiNeighbourhood viewport", () => {
  /** jsdom gives every element a 0x0 rect, and a viewport that divides by that
   *  can only ever produce zeros. Give the SVG a real size so a gesture has
   *  somewhere to land. */
  function sizeTheSvg() {
    const svg = document.querySelector("svg.wiki-nbhd") as SVGSVGElement;
    svg.getBoundingClientRect = () => ({
      x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 250, width: 300, height: 250,
      toJSON: () => ({}),
    }) as DOMRect;
    return svg;
  }

  it("makes each neighbour a link to its own page", async () => {
    await showCross();
    const links = Array.from(document.querySelectorAll("svg a"));
    // Four neighbours are links; the centre is the page you are already on.
    expect(links).toHaveLength(4);
    expect(links.map((a) => a.getAttribute("href")).sort()).toEqual([
      "/programs/p1/wiki/concepts/n1", "/programs/p1/wiki/concepts/n2",
      "/programs/p1/wiki/concepts/n3", "/programs/p1/wiki/concepts/n4",
    ]);
    // The centre's disc is present but not wrapped in a link.
    const hub = screen.getByTitle("Hub hub of the ivywrel correlation");
    expect(hub.closest("a")).toBeNull();
  });

  it("shows the full title of whatever the pointer is over", async () => {
    await showCross();
    const full = "Neighbour n2 of the ivywrel correlation";
    // Truncated in the picture — zooming cannot reveal it, since the text
    // magnifies along with everything else.
    expect(labelsOf().map((t) => t.textContent)).not.toContain(full);

    fireEvent.pointerEnter(screen.getByTitle(full).closest("g")!);

    await waitFor(() =>
      expect(labelsOf().map((t) => t.textContent)).toContain(full));
  });

  it("pans with the pointer, and offers a way back only once moved", async () => {
    await showCross();
    const svg = sizeTheSvg();
    expect(svg.getAttribute("viewBox")).toBe("0 0 300 250");
    expect(screen.queryByText(/reset view/i)).toBeNull();

    fireEvent.pointerDown(svg, { clientX: 100, clientY: 100 });
    fireEvent.pointerMove(svg, { clientX: 140, clientY: 115 });
    fireEvent.pointerUp(svg);

    // Dragging right brings what is to the LEFT into view: the window moves
    // against the hand. A sign error here makes the pane fight the pointer.
    await waitFor(() => expect(svg.getAttribute("viewBox")).toBe("-40 -15 300 250"));
    expect(screen.getByText(/reset view/i)).toBeTruthy();

    fireEvent.click(screen.getByText(/reset view/i));
    await waitFor(() => expect(svg.getAttribute("viewBox")).toBe("0 0 300 250"));
    expect(screen.queryByText(/reset view/i)).toBeNull();
  });

  it("zooms about the cursor on a wheel", async () => {
    await showCross();
    const svg = sizeTheSvg();
    svg.dispatchEvent(new WheelEvent("wheel", {
      deltaY: -100, clientX: 150, clientY: 125, bubbles: true, cancelable: true,
    }));
    await waitFor(() => {
      const [x, y, w, h] = (svg.getAttribute("viewBox") ?? "").split(" ").map(Number);
      expect(w).toBeLessThan(300);              // zoomed in
      expect(h).toBeLessThan(250);
      // The middle of the box was under the cursor, so it must still be.
      expect(x + w / 2).toBeCloseTo(150, 5);
      expect(y + h / 2).toBeCloseTo(125, 5);
    });
  });
});
