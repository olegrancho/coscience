import type { GraphNode, GraphEdge } from "../api";

export interface FlowNode {
  id: string;
  data: { label: string; stage: string; kind: string };
  position: { x: number; y: number };
  style: Record<string, unknown>;
  width?: number;    // size hints for the dagre layout (nodes render their own DOM)
  height?: number;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  data: { edge: GraphEdge };
  animated: boolean;
  style: Record<string, unknown>;
}

const STAGE_COLORS: Record<string, string> = {
  idea: "#8a8f98",         // gray — a candidate direction
  experiment: "#3b82f6",   // blue — running/pending work
  result: "#16a34a",       // green — a finished experiment
};

const SOURCE_COLORS: Record<string, string> = {
  system: "#8a8f98",
  pm: "#3b82f6",
  human: "#a855f7",
};

const EVIDENTIAL = new Set(["confirms", "refutes", "contradicts"]);

export function stageColor(stage: string): string {
  return STAGE_COLORS[stage] ?? "#8a8f98";
}

export function edgeStyle(edge: GraphEdge): { style: Record<string, unknown>; dashed: boolean } {
  const dashed = EVIDENTIAL.has(edge.type);
  const color = SOURCE_COLORS[edge.source] ?? "#8a8f98";
  return {
    dashed,
    style: { stroke: color, strokeWidth: 1.5, strokeDasharray: dashed ? "6 4" : undefined },
  };
}

// The box node is a fixed width; estimate its wrapped height from the label
// length so dagre reserves enough vertical room and ranks don't overlap.
export const NODE_WIDTH = 160;
export function boxHeight(label: string): number {
  const perLine = 24;                       // ~chars per line at 160px / 10px font
  const lines = Math.max(1, Math.ceil(label.length / perLine));
  return 14 + lines * 15;                    // padding + line height
}

export function toFlow(graph: { nodes: GraphNode[]; edges: GraphEdge[] }): {
  nodes: FlowNode[];
  edges: FlowEdge[];
} {
  const nodes: FlowNode[] = graph.nodes.map((n) => {
    const label = n.label || n.id;
    return {
      id: n.id,
      data: { label, stage: n.stage, kind: n.kind },
      position: { x: 0, y: 0 },
      style: {},                              // styling lives in the custom node components
      width: NODE_WIDTH,
      height: boxHeight(label),
    };
  });
  const edges: FlowEdge[] = graph.edges.map((e) => {
    const { style } = edgeStyle(e);
    return {
      id: e.id,
      source: e.src,
      target: e.dst,
      label: e.type,
      data: { edge: e },
      animated: false,
      style,
    };
  });
  return { nodes, edges };
}
