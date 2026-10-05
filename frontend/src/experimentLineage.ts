import type { Graph, GraphNode } from "./api";

/** One experiment's place in its program's lineage (N1), read off the live graph:
 *  what it came from (its own edges) and what followed it (other nodes' edges that
 *  point at it). Nothing is stored on the sprint, so a later experiment that links
 *  back shows up here as soon as its edge exists. */

export type LineageLink = { verb: string; node: GraphNode; edgeId: string; rationale: string };
export type ExperimentLineage = { from: LineageLink[]; followed: LineageLink[] };

// "this <verb> that", for the experiment's own edges...
const OUT: Record<string, string> = {
  inspired_by: "inspired by", builds_on: "builds on", supersedes: "supersedes",
  confirms: "confirms", refutes: "refutes", contradicts: "contradicts",
  refines: "refines", follows: "follows", replicates: "replicates", duplicate_of: "duplicates",
};
// ...and "that <verb> this", for the edges that point at it.
const IN: Record<string, string> = {
  inspired_by: "inspired by this", builds_on: "builds on this", supersedes: "supersedes this",
  confirms: "confirms this", refutes: "refutes this", contradicts: "contradicts this",
  refines: "refines this", follows: "follows this", replicates: "replicates this",
  duplicate_of: "duplicates this",
};

export function experimentLineage(graph: Graph | undefined, id: string): ExperimentLineage {
  const out: ExperimentLineage = { from: [], followed: [] };
  if (!graph) return out;
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  // One row per other node: a sprint that both builds on and confirms this one reads
  // "builds on · confirms", not as two rows naming the same sprint.
  const add = (list: LineageLink[], verb: string, other: string, edgeId: string, why: string) => {
    const row = list.find((l) => l.node.id === other);
    if (!row) { list.push({ verb, node: byId.get(other)!, edgeId, rationale: why }); return; }
    if (!row.verb.split(" · ").includes(verb)) row.verb += ` · ${verb}`;
    if (why && !row.rationale.includes(why)) row.rationale = row.rationale ? `${row.rationale}\n\n${why}` : why;
  };
  for (const e of graph.edges) {
    if (e.src === id && byId.has(e.dst)) add(out.from, OUT[e.type] ?? e.type, e.dst, e.id, e.rationale);
    else if (e.dst === id && byId.has(e.src)) add(out.followed, IN[e.type] ?? e.type, e.src, e.id, e.rationale);
  }
  return out;
}
