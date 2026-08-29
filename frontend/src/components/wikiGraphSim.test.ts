import { describe, it, expect } from "vitest";
import { createWikiSim, type SimLink } from "./wikiGraphSim";

const ids = (n: number) => Array.from({ length: n }, (_, i) => String.fromCharCode(97 + i));
const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.hypot(a.x - b.x, a.y - b.y);
const at = (sim: ReturnType<typeof createWikiSim>, id: string) =>
  sim.nodes().find((n) => n.id === id)!;

describe("createWikiSim", () => {
  // Six nodes, not three: with only three, charge and the seed arrangement
  // alone satisfy a "linked pair is closer" assertion, so the test passes even
  // with forceLink removed entirely. Measured at six nodes — linked pair
  // settles at ~95 (the 90 link distance), an unlinked pair at ~395, and with
  // the link force removed the "linked" pair sits at ~351, indistinguishable
  // from unlinked. The thresholds below are chosen to separate those.
  const LINKED_MAX = 150;

  it("pulls a linked pair to roughly the link distance, far closer than an unlinked pair", () => {
    const links: SimLink[] = [{ source: "a", target: "b" }];
    const sim = createWikiSim(ids(6), links, {});
    sim.settle(400);
    const ab = dist(at(sim, "a"), at(sim, "b"));
    expect(ab).toBeLessThan(LINKED_MAX);
    expect(ab).toBeLessThan(0.5 * dist(at(sim, "a"), at(sim, "c")));
    sim.stop();
  });

  it("holds a node exactly where a drag puts it, across further ticks", () => {
    const sim = createWikiSim(ids(3), [{ source: "a", target: "b" }], {});
    sim.settle(50);
    sim.dragStart("a");
    sim.dragTo("a", 500, -250);
    sim.settle(100);
    expect(at(sim, "a").x).toBe(500);
    expect(at(sim, "a").y).toBe(-250);
    sim.stop();
  });

  it("reheats on drag start, so neighbours reflow instead of staying frozen", () => {
    // Without the alphaTarget reheat, a settled simulation is cold and the
    // dragged node's neighbours never move — the defect this guards.
    // alphaTarget does not change alpha instantly: alpha decays *toward* it,
    // so the reheat is only observable after the simulation advances.
    const sim = createWikiSim(ids(3), [{ source: "a", target: "b" }], {});
    sim.settle(400);
    const coldAlpha = sim.alpha();
    sim.dragStart("a");
    sim.settle(5);
    expect(sim.alpha()).toBeGreaterThan(coldAlpha);
    sim.stop();
  });

  it("drags a linked neighbour along, while an unlinked node is left behind", () => {
    // Asserts distance TO the dragged node, not how far each node moved:
    // forceCenter re-centres the whole graph when one node is hauled away, so
    // everything moves, unconnected nodes included. What the link force buys is
    // that `b` follows `a` to within the link distance and `c` does not.
    const sim = createWikiSim(ids(6), [{ source: "a", target: "b" }], {});
    sim.settle(400);
    sim.dragStart("a");
    sim.dragTo("a", 900, 900);
    sim.settle(300);
    const a = at(sim, "a");
    expect(dist(at(sim, "b"), a)).toBeLessThan(LINKED_MAX);
    expect(dist(at(sim, "b"), a)).toBeLessThan(0.5 * dist(at(sim, "c"), a));
    sim.stop();
  });

  it("keeps a dropped node pinned, so a drag is remembered", () => {
    const sim = createWikiSim(ids(3), [{ source: "a", target: "b" }], {});
    sim.dragStart("a");
    sim.dragTo("a", 400, 100);
    sim.dragEnd("a");
    sim.settle(300);
    expect(at(sim, "a").x).toBe(400);
    expect(at(sim, "a").y).toBe(100);
    expect(sim.pinned()).toEqual({ a: { x: 400, y: 100 } });
    sim.stop();
  });

  it("restores pinned positions given at construction", () => {
    const sim = createWikiSim(ids(3), [{ source: "a", target: "b" }], {
      pinned: { b: { x: -300, y: 700 } },
    });
    sim.settle(200);
    expect(at(sim, "b").x).toBe(-300);
    expect(at(sim, "b").y).toBe(700);
    sim.stop();
  });

  it("unpinAll frees every pinned node to move again", () => {
    const sim = createWikiSim(ids(3), [{ source: "a", target: "b" }], {
      pinned: { b: { x: -300, y: 700 } },
    });
    sim.settle(50);
    sim.unpinAll();
    sim.settle(300);
    expect(at(sim, "b").x).not.toBe(-300);
    expect(sim.pinned()).toEqual({});
    sim.stop();
  });

  it("notifies a tick subscriber as the simulation runs", () => {
    let ticks = 0;
    const sim = createWikiSim(ids(3), [], {});
    sim.onTick(() => { ticks += 1; });
    sim.settle(5);
    expect(ticks).toBeGreaterThan(0);
    sim.stop();
  });

  it("ignores a link whose endpoint is not a node, rather than throwing", () => {
    // d3-force throws on an unresolvable link id; a filtered or stale edge
    // must not take the whole view down.
    const sim = createWikiSim(ids(2), [{ source: "a", target: "ghost" }], {});
    expect(() => sim.settle(10)).not.toThrow();
    sim.stop();
  });
});
