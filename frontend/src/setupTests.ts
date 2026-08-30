// jsdom shims for @xyflow/react.
//
// React Flow measures its viewport and nodes through APIs jsdom does not
// implement. Without these, mounting it throws rather than rendering, which is
// why the older LineageGraph was only ever tested by asserting it stayed
// unmounted. Node dimensions are still zero here, so tests must assert on
// rendered content and handlers, never on computed geometry.

// React Flow will not lay out edges until it has measured both endpoint nodes,
// and it measures through ResizeObserver. A stub that never fires leaves every
// node at zero size and NO edges render at all — so the observer must report a
// plausible size once, or edge assertions silently test an empty canvas.
class ResizeObserverStub {
  constructor(private cb: ResizeObserverCallback) {}
  observe(target: Element) {
    const rect = { width: 60, height: 20, top: 0, left: 0, bottom: 20, right: 60, x: 0, y: 0 };
    queueMicrotask(() => this.cb(
      [{ target, contentRect: rect as DOMRectReadOnly } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    ));
  }
  unobserve() {}
  disconnect() {}
}

class DOMMatrixReadOnlyStub {
  m22 = 1;
  constructor(_transform?: string) {}
}

if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as Record<string, unknown>).ResizeObserver = ResizeObserverStub;
}
if (!("DOMMatrixReadOnly" in globalThis)) {
  (globalThis as unknown as Record<string, unknown>).DOMMatrixReadOnly = DOMMatrixReadOnlyStub;
}

// React Flow reads these off every node element while measuring.
if (!Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight")?.get) {
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { get() { return parseFloat(this.style.height) || 1; } },
    offsetWidth: { get() { return parseFloat(this.style.width) || 1; } },
  });
}
if (!(globalThis as unknown as Record<string, unknown>).DOMRect) {
  (globalThis as unknown as Record<string, unknown>).DOMRect = class {
    constructor(public x = 0, public y = 0, public width = 0, public height = 0) {}
    static fromRect(o?: { x?: number; y?: number; width?: number; height?: number }) {
      return new (globalThis as never as { DOMRect: new (...a: number[]) => unknown }).DOMRect(
        o?.x ?? 0, o?.y ?? 0, o?.width ?? 0, o?.height ?? 0);
    }
    toJSON() { return this; }
  };
}

// jsdom has no PointerEvent. Without it, fireEvent.pointerDown/Move fall back
// to a bare Event and drop clientX/clientY, so any pan test silently measures
// undefined - undefined = NaN rather than the gesture it wrote. MouseEvent
// carries the coordinates and is close enough for these.
if (!("PointerEvent" in globalThis)) {
  (globalThis as unknown as Record<string, unknown>).PointerEvent = MouseEvent;
  (globalThis as unknown as { window?: Record<string, unknown> }).window!.PointerEvent =
    MouseEvent;
}
