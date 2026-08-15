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
  // Newest first: reverse id order so v5 appears above v1.
  const byIdDesc = (a: ArtifactVersionT, b: ArtifactVersionT) => (a.id > b.id ? -1 : a.id < b.id ? 1 : 0);
  const out: TreeRow[] = [];
  // Depth counts forks above a version, not links: an only child continues its
  // parent's line at the same indent. Otherwise a plain linear history — the
  // common case — renders as a staircase whose indentation says nothing.
  //
  // Walk newest-first: children before parents so the most recent version is on
  // top. Within each fork, newest child comes first.
  const walk = (v: ArtifactVersionT, depth: number) => {
    const kids = (children.get(v.id) ?? []).slice().sort(byIdDesc);
    for (const c of kids) walk(c, kids.length > 1 ? depth + 1 : depth);
    out.push({ v, depth, onCurrentPath: path.has(v.id) });
  };
  for (const r of roots.slice().sort(byIdDesc)) walk(r, 0);
  return out;
}
