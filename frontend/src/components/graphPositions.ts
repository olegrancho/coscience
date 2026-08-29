// Per-program manual node positions, persisted in the browser. Kept in its own
// module (no React Flow import) so LineageCard can reset positions without
// pulling the lazy graph chunk into the main bundle.
//
// Two graphs use this now — the lineage graph and the wiki concept graph — so
// every entry point takes an optional namespace. It defaults to "lineage" so
// the original callers, and anything already in a user's localStorage, keep
// working untouched.
export type PosMap = Record<string, { x: number; y: number }>;

export type PosNamespace = "lineage" | "wiki-graph";

const key = (pid: string, ns: PosNamespace) => `${ns}-pos:${pid}`;

export function loadPositions(pid: string, ns: PosNamespace = "lineage"): PosMap {
  try { return JSON.parse(localStorage.getItem(key(pid, ns)) || "{}"); } catch { return {}; }
}

export function savePosition(
  pid: string, id: string, p: { x: number; y: number }, ns: PosNamespace = "lineage",
): void {
  try {
    const m = loadPositions(pid, ns);
    m[id] = p;
    localStorage.setItem(key(pid, ns), JSON.stringify(m));
  } catch { /* storage unavailable; positions just won't persist */ }
}

/** Replace the whole map at once — what the wiki graph's simulation hands back
 *  after a drag, since it tracks every pinned node rather than one at a time. */
export function savePositions(pid: string, m: PosMap, ns: PosNamespace = "lineage"): void {
  try { localStorage.setItem(key(pid, ns), JSON.stringify(m)); } catch { /* ignore */ }
}

export function clearPositions(pid: string, ns: PosNamespace = "lineage"): void {
  try { localStorage.removeItem(key(pid, ns)); } catch { /* ignore */ }
}
