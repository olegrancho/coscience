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

describe("pin", () => {
  it("holds a node where it is put, through any number of ticks", () => {
    const sim = createWikiSim(["a", "b", "c"],
      [{ source: "a", target: "b" }, { source: "b", target: "c" }], {});
    sim.pin("a", 40, -25);
    sim.settle(200);
    const a = sim.nodes().find((n) => n.id === "a")!;
    expect(a.x).toBe(40);
    expect(a.y).toBe(-25);
    // ...while everything else is still free to arrange itself.
    const b = sim.nodes().find((n) => n.id === "b")!;
    expect(Math.hypot(b.x - 40, b.y + 25)).toBeGreaterThan(1);
  });

  it("reports a pinned node as pinned, so the view can persist it", () => {
    const sim = createWikiSim(["a", "b"], [], {});
    sim.pin("a", 7, 8);
    expect(sim.pinned()).toEqual({ a: { x: 7, y: 8 } });
  });

  it("unpinAll releases it again", () => {
    const sim = createWikiSim(["a", "b"], [{ source: "a", target: "b" }], {});
    sim.pin("a", 300, 300);
    sim.unpinAll();
    sim.settle(300);
    expect(sim.pinned()).toEqual({});
    // Freed, the centring force pulls it back in from 300,300.
    const a = sim.nodes().find((n) => n.id === "a")!;
    expect(Math.hypot(a.x, a.y)).toBeLessThan(200);
  });

  it("ignores a node it does not have", () => {
    const sim = createWikiSim(["a"], [], {});
    expect(() => sim.pin("nope", 1, 2)).not.toThrow();
    expect(sim.pinned()).toEqual({});
  });
});

describe("tuning", () => {
  it("honours a shorter link distance, so a small pane can use the same physics", () => {
    const spread = (linkDistance: number) => {
      const sim = createWikiSim(["a", "b"], [{ source: "a", target: "b" }],
        { linkDistance, charge: -60, collide: 5 });
      sim.settle(400);
      const [a, b] = sim.nodes();
      return Math.hypot(a.x - b.x, a.y - b.y);
    };
    const tight = spread(30);
    const loose = spread(120);
    expect(tight).toBeLessThan(loose);
    // And it actually lands near the distance asked for, not merely "less".
    expect(tight).toBeGreaterThan(20);
    expect(tight).toBeLessThan(45);
  });
});

describe("seed", () => {
  it("starts a node where told, without pinning it there", () => {
    const sim = createWikiSim(["a", "b"], [{ source: "a", target: "b" }],
      { seed: { a: { x: 111, y: 222 } } });
    expect(sim.nodes().find((n) => n.id === "a")!.x).toBe(111);
    expect(sim.pinned()).toEqual({});     // seeded, not held
    sim.settle(300);
    expect(sim.nodes().find((n) => n.id === "a")!.x).not.toBe(111);
  });
});
