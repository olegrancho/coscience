import type { ArtifactVersionT } from "../api";

export interface TreeRow { v: ArtifactVersionT; depth: number; onCurrentPath: boolean }

export function buildArtifactTree(versions: ArtifactVersionT[], current: string): TreeRow[] {
  const byId = new Map(versions.map((v) => [v.id, v]));
  const ids = new Set(byId.keys());
  // ancestors of `current` (inclusive) — the highlighted path
  const path = new Set<string>();
  let cur: string | undefined = current;
  while (cur && byId.has(cur) && !path.has(cur)) {
    path.add(cur);
    cur = byId.get(cur)!.parent;
  }
  // children index; a version whose parent is missing is a root
  const children = new Map<string, ArtifactVersionT[]>();
  const roots: ArtifactVersionT[] = [];
  for (const v of versions) {
    if (v.parent && ids.has(v.parent)) {
      if (!children.has(v.parent)) children.set(v.parent, []);
      children.get(v.parent)!.push(v);
    } else {
      roots.push(v);
    }
  }
  const byIdOrder = (a: ArtifactVersionT, b: ArtifactVersionT) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const out: TreeRow[] = [];
  const walk = (v: ArtifactVersionT, depth: number) => {
    out.push({ v, depth, onCurrentPath: path.has(v.id) });
    for (const c of (children.get(v.id) ?? []).slice().sort(byIdOrder)) walk(c, depth + 1);
  };
  for (const r of roots.slice().sort(byIdOrder)) walk(r, 0);
  return out;
}
