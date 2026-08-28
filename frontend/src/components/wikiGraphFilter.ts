import type { WikiGraphT } from "../api";

export interface Filters {
  types: Set<string>;       // empty = all
  relations: Set<string>;   // empty = all
  trust: Set<string>;       // empty = all
  typedOnly: boolean;
}

export function emptyFilters(): Filters {
  return { types: new Set(), relations: new Set(), trust: new Set(), typedOnly: false };
}

/** Hides; never re-layouts and never recomputes `orphan`.
 *  `orphan` is a whole-graph property from the server — deriving it from the
 *  filtered edge set would ring nodes the reader merely hid, reporting a data
 *  problem that does not exist (design 5.3). Node objects pass through
 *  untouched for exactly that reason. */
export function applyFilters(g: WikiGraphT, f: Filters): WikiGraphT {
  const keep = (s: Set<string>, v: string) => s.size === 0 || s.has(v);
  const nodes = g.nodes.filter((n) => keep(f.types, n.type) && keep(f.trust, n.trust));
  const live = new Set(nodes.map((n) => n.id));
  const edges = g.edges.filter((e) =>
    live.has(e.src) && live.has(e.dst)
    && (!f.typedOnly || e.typed)
    && (e.typed ? keep(f.relations, e.type) : f.relations.size === 0));
  return { nodes, edges };
}
