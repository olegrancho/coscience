// Reconciling two coordinate conventions in the wiki concept graph.
//
// The simulation places a node by its *centre*; React Flow places one by its
// top-left *corner* and anchors edges wherever it measures the node's handles.
// This graph pins its handles to the middle of the disc — that is what makes
// an edge come out of the node rather than out of the node's label — so both
// problems reduce to the same arithmetic: shift between centre and corner, and
// trim a centre-to-centre line back to the two rims so an arrowhead is not
// buried under the disc it points at. Trimming is also what makes the two
// directions of a `contradicts` pair legible: a head at each rim, not two
// heads hidden inside two circles.
//
// Pure geometry, no React and no DOM: jsdom renders no React Flow edges and
// fires no drags, so this is the only place either can actually be tested.

export interface Segment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** Gap in px left between a disc's rim and the tip of the arrow, so the head
 *  reads as pointing *at* the node rather than touching it. */
export const RIM_GAP = 2;

/**
 * The straight segment from one disc's rim to the other's.
 *
 * `sourceR`/`targetR` are radii, not diameters. Returns `null` when the discs
 * are close enough to overlap: any segment drawn there would be zero-length or
 * reversed, and a reversed segment points its arrowhead into the wrong node.
 * Drawing nothing until they separate is the honest answer — the simulation
 * pushes them apart within a few ticks.
 */
export function rimSegment(
  sx: number, sy: number, tx: number, ty: number,
  sourceR: number, targetR: number, gap: number = RIM_GAP,
): Segment | null {
  const dx = tx - sx;
  const dy = ty - sy;
  const len = Math.hypot(dx, dy);
  if (len <= sourceR + targetR + 2 * gap) return null;
  const ux = dx / len;
  const uy = dy / len;
  return {
    x1: sx + ux * (sourceR + gap),
    y1: sy + uy * (sourceR + gap),
    x2: tx - ux * (targetR + gap),
    y2: ty - uy * (targetR + gap),
  };
}

export function segmentPath(s: Segment): string {
  return `M ${s.x1},${s.y1} L ${s.x2},${s.y2}`;
}

export interface Point { x: number; y: number; }

/** Simulation centre → the top-left corner React Flow wants. `r` is the disc's
 *  diameter, as `nodeSize` reports it. */
export function toCorner(centre: Point, r: number): Point {
  return { x: centre.x - r / 2, y: centre.y - r / 2 };
}

/** The corner React Flow reports back mid-drag → the centre to pin the
 *  simulation to. Exactly the inverse of `toCorner`; a sign error here drifts
 *  every node by half its diameter the moment it is grabbed. */
export function toCentre(corner: Point, r: number): Point {
  return { x: corner.x + r / 2, y: corner.y + r / 2 };
}
