// Per-program manual node positions, persisted in the browser. Kept in its own
// module (no React Flow import) so LineageCard can reset positions without
// pulling the lazy graph chunk into the main bundle.
export type PosMap = Record<string, { x: number; y: number }>;

const key = (pid: string) => `lineage-pos:${pid}`;

export function loadPositions(pid: string): PosMap {
  try { return JSON.parse(localStorage.getItem(key(pid)) || "{}"); } catch { return {}; }
}

export function savePosition(pid: string, id: string, p: { x: number; y: number }): void {
  try {
    const m = loadPositions(pid);
    m[id] = p;
    localStorage.setItem(key(pid), JSON.stringify(m));
  } catch { /* storage unavailable; positions just won't persist */ }
}

export function clearPositions(pid: string): void {
  try { localStorage.removeItem(key(pid)); } catch { /* ignore */ }
}
