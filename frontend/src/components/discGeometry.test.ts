import { describe, it, expect } from "vitest";
import { rimSegment, segmentPath, toCorner, toCentre, RIM_GAP } from "./discGeometry";

describe("rimSegment", () => {
  it("starts and ends on the rims, not at the centres", () => {
    // Centres 100 apart, radii 10 and 20, gap 2 ⇒ the line runs 12..78.
    const s = rimSegment(0, 0, 100, 0, 10, 20, 2)!;
    expect(s).not.toBeNull();
    expect(s.x1).toBeCloseTo(12);
    expect(s.x2).toBeCloseTo(78);
    expect(s.y1).toBeCloseTo(0);
    expect(s.y2).toBeCloseTo(0);
    // Untrimmed (the bug this exists to prevent) these would be 0 and 100 —
    // the arrowhead sitting at the target's centre, under its disc.
    expect(s.x1).toBeGreaterThan(0);
    expect(s.x2).toBeLessThan(100);
  });

  it("clears each disc by exactly its own radius plus the gap, in any direction", () => {
    // 3-4-5: centres 50 apart on a diagonal, so a bug that only trims on one
    // axis, or that uses the wrong node's radius, shows up here.
    const s = rimSegment(10, 10, 40, 50, 6, 18, 2)!;
    expect(Math.hypot(s.x1 - 10, s.y1 - 10)).toBeCloseTo(8);   // 6 + 2
    expect(Math.hypot(s.x2 - 40, s.y2 - 50)).toBeCloseTo(20);  // 18 + 2
    // ...and both ends still lie on the centre-to-centre line.
    expect((s.x1 - 10) / (s.y1 - 10)).toBeCloseTo(30 / 40);
    expect((s.x2 - 10) / (s.y2 - 10)).toBeCloseTo(30 / 40);
  });

  it("points the same way as the centre-to-centre direction", () => {
    const s = rimSegment(0, 0, -60, 80, 10, 10)!;
    const dot = (s.x2 - s.x1) * -60 + (s.y2 - s.y1) * 80;
    expect(dot).toBeGreaterThan(0);
  });

  it("draws nothing while the discs overlap, rather than a backwards arrow", () => {
    // 25 apart but 20 + 20 of radius: an untrimmed-length check would happily
    // return a segment running from x=22 back to x=3, arrowhead pointing at
    // the source.
    expect(rimSegment(0, 0, 25, 0, 20, 20)).toBeNull();
    expect(rimSegment(0, 0, 0, 0, 5, 5)).toBeNull();
    // Exactly touching (rims + both gaps == distance) is zero length: also nothing.
    expect(rimSegment(0, 0, 10 + 10 + 2 * RIM_GAP, 0, 10, 10)).toBeNull();
  });

  it("never returns a reversed segment at any separation it accepts", () => {
    // Sweep the region just past the threshold, where a sign error hides.
    for (let d = 1; d < 200; d += 1) {
      const s = rimSegment(0, 0, d, 0, 14, 9);
      if (s) expect(s.x2).toBeGreaterThan(s.x1);
    }
  });

  it("renders an SVG line command", () => {
    expect(segmentPath({ x1: 1, y1: 2, x2: 3, y2: 4 })).toBe("M 1,2 L 3,4");
  });
});

describe("centre / corner conversion", () => {
  it("places the disc so its centre lands on the simulation's coordinate", () => {
    expect(toCorner({ x: 100, y: 40 }, 30)).toEqual({ x: 85, y: 25 });
  });

  it("round-trips, so a drag reports back the centre it was given", () => {
    for (const r of [14, 21, 40]) {
      for (const p of [{ x: 0, y: 0 }, { x: -37.5, y: 812 }, { x: 6, y: -6 }]) {
        expect(toCentre(toCorner(p, r), r)).toEqual(p);
        // A drag that does not move must not shift the node: this is the
        // assertion that a + / - mix-up in the view would break.
        expect(toCorner(toCentre(toCorner(p, r), r), r)).toEqual(toCorner(p, r));
      }
    }
  });
});
