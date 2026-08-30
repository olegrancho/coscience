import type { WikiGraphT } from "../api";

/** What the reader has actually chosen.
 *
 *  `null` means "has not touched this group", NOT "nothing selected" — the two
 *  used to be the same empty set, and that is what made the controls lie: with
 *  every box blank the graph showed everything, and ticking one box then
 *  narrowed to just that one. Reading it as a selection made "all off" and
 *  "all on" the same state.
 *
 *  `null` is resolved against the options actually present in the graph
 *  (`resolveFilters`) before anything filters, so from that point on a set
 *  means exactly what it says: these values are shown, and an empty set shows
 *  none of them. */
export interface Filters {
  types: Set<string> | null;
  relations: Set<string> | null;
  trust: Set<string> | null;
  typedOnly: boolean;
}

/** The groups a graph offers, read off the data. */
export interface FilterOptions {
  types: string[];
  relations: string[];
  trust: string[];
}

export type ResolvedFilters = {
  types: Set<string>;
  relations: Set<string>;
  trust: Set<string>;
  typedOnly: boolean;
};

export function emptyFilters(): Filters {
  return { types: null, relations: null, trust: null, typedOnly: false };
}

export function filterOptions(g: WikiGraphT): FilterOptions {
  return {
    types: Array.from(new Set(g.nodes.map((n) => n.type))).sort(),
    trust: Array.from(new Set(g.nodes.map((n) => n.trust))).sort(),
    relations: Array.from(
      new Set(g.edges.filter((e) => e.typed && e.type).map((e) => e.type))).sort(),
  };
}

/** Untouched groups become "all of them", so the controls can be rendered from
 *  the same sets that do the filtering — which is what keeps the ticks and the
 *  picture in agreement. */
export function resolveFilters(f: Filters, o: FilterOptions): ResolvedFilters {
  return {
    types: f.types ?? new Set(o.types),
    relations: f.relations ?? new Set(o.relations),
    trust: f.trust ?? new Set(o.trust),
    typedOnly: f.typedOnly,
  };
}

/** Flip one option, starting from what is actually on screen. Passing the
 *  resolved set is the point: clicking one chip while everything is on must
 *  turn that one OFF, not turn everything else off. */
export function toggleOption(shown: Set<string>, v: string): Set<string> {
  const next = new Set(shown);
  if (next.has(v)) next.delete(v); else next.add(v);
  return next;
}

/** Whether a group is at "everything", so the UI can offer `all`/`none` without
 *  guessing. */
export function isAll(shown: Set<string>, all: string[]): boolean {
  return all.every((v) => shown.has(v));
}

/** Hides; never re-layouts and never recomputes `orphan`.
 *  `orphan` is a whole-graph property from the server — deriving it from the
 *  filtered edge set would ring nodes the reader merely hid, reporting a data
 *  problem that does not exist (design 5.3). Node objects pass through
 *  untouched for exactly that reason.
 *
 *  An untyped edge has no relation to match, so it answers to `typedOnly`
 *  alone. It used to also disappear the moment any relation was ticked, which
 *  made a control labelled "refines" silently hide every body link too. */
export function applyFilters(g: WikiGraphT, f: ResolvedFilters): WikiGraphT {
  const nodes = g.nodes.filter((n) => f.types.has(n.type) && f.trust.has(n.trust));
  const live = new Set(nodes.map((n) => n.id));
  const edges = g.edges.filter((e) =>
    live.has(e.src) && live.has(e.dst)
    && (!f.typedOnly || e.typed)
    && (!e.typed || f.relations.has(e.type)));
  return { nodes, edges };
}
