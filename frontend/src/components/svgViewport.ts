// Pan and zoom for a hand-drawn SVG, expressed entirely as arithmetic on the
// viewBox.
//
// The neighbourhood pane draws its own SVG rather than mounting React Flow —
// that is deliberate, and load-bearing: React Flow and d3-force are what the
// browse view's bundle is kept clear of (see radialLayout.ts). So the viewport
// is ours to implement, and it lives here, pure, because a component cannot be
// asked in jsdom what a wheel gesture did to it.

export interface View { x: number; y: number; w: number; h: number; }

/** How far in and out the reader may go, as a multiple of the base view. Out
 *  is limited too: past a point the picture is a speck in an empty field and
 *  there is nothing to find by going further. */
export const MIN_ZOOM = 0.6;
export const MAX_ZOOM = 6;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** The current magnification, 1 being the whole picture at rest. */
export function zoomOf(v: View, base: View): number {
  return v.w === 0 ? 1 : base.w / v.w;
}

/**
 * Zoom by `factor` about the world point (`px`, `py`), which stays put on
 * screen — the behaviour that makes wheel-zoom feel attached to the cursor
 * rather than to the middle of the box.
 */
export function zoomAt(
  v: View, px: number, py: number, factor: number, base: View,
): View {
  const zoom = clamp(zoomOf(v, base) * factor, MIN_ZOOM, MAX_ZOOM);
  const w = base.w / zoom;
  const h = base.h / zoom;
  // Where the anchor sits in the current box, as a fraction. Keeping that
  // fraction fixed is what pins it under the cursor.
  const fx = v.w === 0 ? 0.5 : (px - v.x) / v.w;
  const fy = v.h === 0 ? 0.5 : (py - v.y) / v.h;
  return { x: px - fx * w, y: py - fy * h, w, h };
}

/** Drag the picture with the pointer: the world moves WITH the hand, so the
 *  window moves against it. */
export function panBy(v: View, dxWorld: number, dyWorld: number): View {
  return { ...v, x: v.x - dxWorld, y: v.y - dyWorld };
}

/** Screen pixels → world units. A zero-sized element (jsdom, or a pane not yet
 *  laid out) would otherwise divide by zero and put the view at NaN, which
 *  blanks the SVG for good. */
export function toWorld(v: View, rect: { width: number; height: number }): { sx: number; sy: number } {
  return {
    sx: rect.width > 0 ? v.w / rect.width : 0,
    sy: rect.height > 0 ? v.h / rect.height : 0,
  };
}

export function viewBox(v: View): string {
  return `${v.x} ${v.y} ${v.w} ${v.h}`;
}
