import { useCallback, useEffect, useMemo } from "react";
import {
  ReactFlow, Background, Controls, MarkerType, BaseEdge, EdgeLabelRenderer,
  getBezierPath, useNodesState, useStore, Handle, Position,
  type EdgeProps, type NodeProps, type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Graph, GraphEdge } from "../api";
import { toFlow, stageColor, NODE_WIDTH } from "./graphFlow";
import { layout } from "./graphLayout";
import { loadPositions, savePosition } from "./graphPositions";

export type LayoutMode = "box" | "dot";

type NodeData = { label: string; stage: string; kind: string };

function edgeTitle(ge: GraphEdge): string {
  return [
    ge.type,
    ge.rationale && `— ${ge.rationale}`,
    ge.confidence && `(confidence: ${ge.confidence})`,
    ge.by && `by ${ge.by}`,
  ].filter(Boolean).join(" ");
}

// --- custom nodes ---
function BoxNode({ data }: NodeProps) {
  const d = data as NodeData;
  return (
    <div
      style={{
        border: `2px solid ${stageColor(d.stage)}`,
        borderRadius: 8,
        padding: "5px 8px",
        width: NODE_WIDTH,
        fontSize: 10,
        lineHeight: 1.3,
        textAlign: "center",
        whiteSpace: "normal",
        wordBreak: "break-word",
        background: "var(--mantine-color-body, #fff)",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ visibility: "hidden" }} />
      {d.label}
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />
    </div>
  );
}

function DotNode({ data }: NodeProps) {
  const zoom = useStore((s) => s.transform[2]);
  const d = data as NodeData;
  const showLabel = zoom >= 0.6;   // hide side labels when zoomed out
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Handle type="target" position={Position.Top} style={{ visibility: "hidden" }} />
      <span
        title={d.label}
        style={{
          width: 12, height: 12, borderRadius: "50%",
          background: stageColor(d.stage), flex: "0 0 auto",
          border: "1px solid rgba(0,0,0,0.15)",
        }}
      />
      {showLabel && (
        <span style={{ fontSize: 10, whiteSpace: "nowrap", color: "var(--mantine-color-text, #222)" }}>
          {d.label}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />
    </div>
  );
}

const nodeTypes = { box: BoxNode, dot: DotNode };

// --- custom edge: real HTML label (EdgeLabelRenderer) so the hover title fires ---
function LineageEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  markerEnd, style, data,
}: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  });
  const ge = (data as { edge: GraphEdge } | undefined)?.edge;
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {ge && (
        <EdgeLabelRenderer>
          <div
            title={edgeTitle(ge)}
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              fontSize: 10,
              padding: "0 3px",
              borderRadius: 4,
              background: "var(--mantine-color-body, #fff)",
              color: "var(--mantine-color-dimmed, #666)",
              pointerEvents: "all",
              cursor: "default",
            }}
          >
            {ge.type}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const edgeTypes = { lineage: LineageEdge };

export default function LineageGraph({
  graph,
  programId,
  mode = "box",
  onNodeClick,
}: {
  graph: Graph;
  programId: string;
  mode?: LayoutMode;
  onNodeClick?: (nodeId: string) => void;
}) {
  // Base dagre layout (positions computed from graph structure).
  const base = useMemo(() => {
    const flow = toFlow(graph);
    return layout(flow.nodes, flow.edges);
  }, [graph]);

  const edges = useMemo(
    () =>
      toFlow(graph).edges.map((e) => ({
        ...e,
        type: "lineage",
        markerEnd: { type: MarkerType.ArrowClosed },
        ariaLabel: edgeTitle(e.data.edge),
      })),
    [graph],
  );

  // Build the render nodes: saved position (if the user moved it) overrides dagre.
  const buildNodes = useCallback(
    (m: LayoutMode): Node[] => {
      const saved = loadPositions(programId);
      return base.map((n) => ({
        id: n.id,
        type: m,
        position: saved[n.id] ?? n.position,
        data: n.data,
      }));
    },
    [base, programId],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(buildNodes(mode));

  // Rebuild when the graph data or the layout mode changes (keeps saved positions).
  useEffect(() => {
    setNodes(buildNodes(mode));
  }, [buildNodes, mode, setNodes]);

  const onNodeDragStop = useCallback(
    (_: unknown, node: Node) => savePosition(programId, node.id, node.position),
    [programId],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onNodeDragStop={onNodeDragStop}
      fitView
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, node) => onNodeClick?.(node.id)}
    >
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
