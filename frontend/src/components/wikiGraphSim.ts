// A live d3-force simulation for the wiki concept graph.
//
// Phase 4 originally ran the layout for a fixed number of ticks and threw the
// simulation away, which produced a still photograph: nothing moved and nothing
// could be grabbed. This keeps the simulation alive so dragging a node shoves
// its neighbours around and the graph re-settles — the behaviour Obsidian's
// graph view has, built from the same three forces (repel, link, centre).
//
// The module owns no DOM and no React. It exposes positions plus a tick
// subscription, so the view can render from it and the physics can be tested
// headlessly.
import {
  forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide,
  type Simulation, type SimulationNodeDatum,
} from "d3-force";

export interface SimNode extends SimulationNodeDatum {
  id: string;
  x: number;
  y: number;
}

export interface SimLink {
  source: string;
  target: string;
}

export type Pinned = Record<string, { x: number; y: number }>;

export interface WikiSimOptions {
  /** Positions restored from a previous session; these load pinned. */
  pinned?: Pinned;
  /** Called on every tick, for the view to re-render from. */
  onTick?: () => void;
}

export interface WikiSim {
  nodes(): SimNode[];
  pinned(): Pinned;
  onTick(cb: () => void): void;
  dragStart(id: string): void;
  dragTo(id: string, x: number, y: number): void;
  dragEnd(id: string): void;
  unpinAll(): void;
  alpha(): number;
  /** Advance deterministically without the timer — used by tests. */
  settle(ticks: number): void;
  stop(): void;
}

const LINK_DISTANCE = 90;
const CHARGE = -240;
const COLLIDE = 28;
const DRAG_ALPHA = 0.3;

/** Deterministic phyllotaxis seed, matching d3's own initial placement. Only
 *  the starting arrangement is fixed — the simulation is live from there. */
function seed(i: number): { x: number; y: number } {
  const r = 10 * Math.sqrt(0.5 + i);
  const a = i * Math.PI * (3 - Math.sqrt(5));
  return { x: r * Math.cos(a), y: r * Math.sin(a) };
}

export function createWikiSim(
  ids: string[], links: SimLink[], opts: WikiSimOptions,
): WikiSim {
  const known = new Set(ids);
  const nodes: SimNode[] = ids.map((id, i) => {
    const p = opts.pinned?.[id];
    const s = seed(i);
    return p
      ? { id, x: p.x, y: p.y, fx: p.x, fy: p.y }
      : { id, x: s.x, y: s.y };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // d3-force throws on a link whose endpoint it cannot resolve. A filtered or
  // stale edge must not take the whole view down, so drop those here.
  const live = links.filter((l) => known.has(l.source) && known.has(l.target));

  const subscribers: Array<() => void> = [];
  if (opts.onTick) subscribers.push(opts.onTick);

  const sim: Simulation<SimNode, undefined> = forceSimulation(nodes)
    .force("link", forceLink<SimNode, never>(live as never[])
      .id((d) => (d as SimNode).id).distance(LINK_DISTANCE))
    .force("charge", forceManyBody().strength(CHARGE))
    .force("centre", forceCenter(0, 0))
    .force("collide", forceCollide(COLLIDE))
    .on("tick", () => { for (const cb of subscribers) cb(); });

  return {
    nodes: () => nodes,
    pinned: () => Object.fromEntries(
      nodes.filter((n) => n.fx != null && n.fy != null)
           .map((n) => [n.id, { x: n.fx as number, y: n.fy as number }])),
    onTick: (cb) => { subscribers.push(cb); },

    dragStart: (id) => {
      const n = byId.get(id);
      if (!n) return;
      // Reheat, or a settled simulation is cold and the neighbours never move.
      sim.alphaTarget(DRAG_ALPHA).restart();
      n.fx = n.x;
      n.fy = n.y;
    },
    dragTo: (id, x, y) => {
      const n = byId.get(id);
      if (!n) return;
      n.fx = x;
      n.fy = y;
    },
    dragEnd: (id) => {
      // Deliberately does NOT clear fx/fy: a dropped node stays where it was
      // put, which is what "remember my drags" means. `unpinAll` is the escape.
      sim.alphaTarget(0);
      void id;
    },
    unpinAll: () => {
      for (const n of nodes) { n.fx = null; n.fy = null; }
      sim.alpha(DRAG_ALPHA).restart();
    },

    alpha: () => sim.alpha(),
    settle: (ticks) => { sim.tick(ticks); for (const cb of subscribers) cb(); },
    stop: () => { sim.stop(); },
  };
}
