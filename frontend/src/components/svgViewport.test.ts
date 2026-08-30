import { describe, it, expect } from "vitest";
import {
  zoomAt, panBy, toWorld, viewBox, zoomOf, MIN_ZOOM, MAX_ZOOM, type View,
} from "./svgViewport";

const base: View = { x: 0, y: 0, w: 300, h: 250 };

describe("zoomAt", () => {
  it("keeps the point under the cursor exactly where it was", () => {
    // The whole point of zooming about a cursor: whatever was under it stays
    // under it, at any magnification.
    let v = base;
    for (const factor of [1.2, 1.2, 0.8, 2, 0.5]) {
      const before = (v.w === 0 ? 0.5 : (120 - v.x) / v.w);
      v = zoomAt(v, 120, 90, factor, base);
      expect((120 - v.x) / v.w).toBeCloseTo(before, 6);
    }
  });

  it("actually changes the magnification", () => {
    // Guards the vacuous version of the test above, which a no-op zoom passes.
    const inn = zoomAt(base, 150, 125, 2, base);
    expect(zoomOf(inn, base)).toBeCloseTo(2);
    expect(inn.w).toBeCloseTo(150);
    const out = zoomAt(inn, 150, 125, 0.5, base);
    expect(zoomOf(out, base)).toBeCloseTo(1);
  });

  it("stops at the limits instead of running away", () => {
    let v = base;
    for (let i = 0; i < 40; i++) v = zoomAt(v, 150, 125, 1.5, base);
    expect(zoomOf(v, base)).toBeCloseTo(MAX_ZOOM);
    for (let i = 0; i < 80; i++) v = zoomAt(v, 150, 125, 0.7, base);
    expect(zoomOf(v, base)).toBeCloseTo(MIN_ZOOM);
  });
});

describe("panBy", () => {
  it("moves the world with the hand", () => {
    // Dragging right must bring what is to the LEFT into view, so the window
    // origin decreases. The sign error here is the one that makes a pane feel
    // like it is fighting the pointer.
    const v = panBy(base, 40, 15);
    expect(v.x).toBe(-40);
    expect(v.y).toBe(-15);
    expect(v.w).toBe(base.w);
    expect(v.h).toBe(base.h);
  });

  it("round-trips", () => {
    expect(panBy(panBy(base, 40, 15), -40, -15)).toEqual(base);
  });
});

describe("toWorld", () => {
  it("scales screen pixels by how much world each one shows", () => {
    expect(toWorld(base, { width: 150, height: 125 })).toEqual({ sx: 2, sy: 2 });
    expect(toWorld({ ...base, w: 150, h: 125 }, { width: 300, height: 250 }))
      .toEqual({ sx: 0.5, sy: 0.5 });
  });

  it("returns zero rather than NaN for an unlaid-out element", () => {
    // jsdom reports every rect as 0×0. Dividing by that puts the viewBox at
    // NaN, which blanks the SVG permanently — one stray gesture and the pane
    // never comes back.
    expect(toWorld(base, { width: 0, height: 0 })).toEqual({ sx: 0, sy: 0 });
  });
});

describe("viewBox", () => {
  it("renders the SVG attribute", () => {
    expect(viewBox(base)).toBe("0 0 300 250");
  });
});
