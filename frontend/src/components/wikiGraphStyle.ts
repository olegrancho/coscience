// Encoding for the wiki concept graph (design 5.1).
//
// Hue carries page type, fill carries trust. The parent spec 10 says trust is
// for colouring and 11.4 says colour is by type; splitting the channels
// satisfies both. Fill deliberately mirrors .wiki-dot--* in the browse view so
// a marker means the same thing in both places.
import type { WikiGraphNode, WikiGraphEdge } from "../api";

export type Lens = "structure" | "tension";

export const TYPE_HUE: Record<string, string> = {
  Concept: "#3b82f6",     // blue
  Entity: "#a855f7",      // purple
  Synthesis: "#16a34a",   // green
};

const TRUST_FILL: Record<string, string> = {
  "unverified": "transparent",
  "machine-confirmed": "var(--ink-faint)",
  "human-reviewed": "var(--machine)",
};

export const TENSION_TYPES = new Set(["contradicts", "replaces", "refines"]);

export const TENSION_COLOUR: Record<string, string> = {
  contradicts: "#dc2626",
  replaces: "#ea580c",
  refines: "#d97706",
};

export function nodeSize(n: WikiGraphNode): number {
  return 14 + Math.min(26, 4 * Math.sqrt(n.in_degree + n.out_degree));
}

/** `inTension` says whether this node touches a tension edge. Under the tension
 *  lens a node that touches none is dimmed back, so the disagreements are what
 *  the eye lands on (design 5.2). It is optional: callers that do not compute
 *  participation get the undimmed structure styling, which is what the
 *  neighbourhood pane wants. */
export function nodeStyle(
  n: WikiGraphNode, lens: Lens, inTension?: boolean,
): Record<string, string> {
  const deprecated = n.status === "deprecated";
  const quiet = lens === "tension" && inTension === false;
  return {
    borderColor: TYPE_HUE[n.type] ?? "#8a8f98",
    background: TRUST_FILL[n.trust] ?? "transparent",
    outline: n.orphan ? "2px dotted #8a8f98" : "",
    opacity: deprecated ? "0.4" : quiet ? "0.25" : "1",
    textDecoration: deprecated ? "line-through" : "none",
  };
}

export function edgeStyle(e: WikiGraphEdge, lens: Lens): Record<string, string> {
  if (lens === "tension") {
    const loud = TENSION_TYPES.has(e.type);
    return {
      stroke: loud ? (TENSION_COLOUR[e.type] ?? "#dc2626") : "#c9ccd1",
      strokeWidth: loud ? (e.type === "contradicts" ? "3" : "2") : "1",
      strokeDasharray: e.typed ? "" : "3 3",
      opacity: loud ? "1" : (e.typed ? "0.15" : "0.08"),
    };
  }
  return {
    stroke: "#8a8f98",
    strokeWidth: "1",
    strokeDasharray: e.typed ? "" : "3 3",
    opacity: e.typed ? "0.75" : "0.35",
  };
}
