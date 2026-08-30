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

export interface WikiSimTuning {
  /** Rest length of a link. */
  linkDistance?: number;
  /** How hard nodes push each other away; negative repels. */
  charge?: number;
  /** Minimum centre-to-centre distance. */
  collide?: number;
}

export interface WikiSimOptions extends WikiSimTuning {
  /** Positions restored from a previous session; these load pinned. */
  pinned?: Pinned;
  /** Where to START unpinned nodes. Unlike `pinned` these are free to move —
   *  it just saves the simulation from having to undo a layout nobody chose.
   *  The neighbourhood pane seeds from its radial rings for that reason. */
  seed?: Pinned;
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
  /** Hold one node in place. The neighbourhood pane pins the page you are on,
   *  so "you are here" does not wander while its neighbours arrange. */
  pin(id: string, x: number, y: number): void;
  alpha(): number;
  /** Advance deterministically without the timer — used by tests. */
  settle(ticks: number): void;
  stop(): void;
}

// Defaults are tuned for the full-page canvas. The neighbourhood pane is a
// 300px square and overrides all three; at these values it would fling its
// nodes clean out of the box.
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
    if (p) return { id, x: p.x, y: p.y, fx: p.x, fy: p.y };
    const s = opts.seed?.[id] ?? seed(i);
    return { id, x: s.x, y: s.y };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // d3-force throws on a link whose endpoint it cannot resolve. A filtered or
  // stale edge must not take the whole view down, so drop those here.
  const live = links.filter((l) => known.has(l.source) && known.has(l.target));

  const subscribers: Array<() => void> = [];
  if (opts.onTick) subscribers.push(opts.onTick);

  const sim: Simulation<SimNode, undefined> = forceSimulation(nodes)
    .force("link", forceLink<SimNode, never>(live as never[])
      .id((d) => (d as SimNode).id).distance(opts.linkDistance ?? LINK_DISTANCE))
    .force("charge", forceManyBody().strength(opts.charge ?? CHARGE))
    .force("centre", forceCenter(0, 0))
    .force("collide", forceCollide(opts.collide ?? COLLIDE))
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
    pin: (id, x, y) => {
      const n = byId.get(id);
      if (!n) return;
      n.x = x; n.y = y; n.fx = x; n.fy = y;
    },

    alpha: () => sim.alpha(),
    settle: (ticks) => { sim.tick(ticks); for (const cb of subscribers) cb(); },
    stop: () => { sim.stop(); },
  };
}
