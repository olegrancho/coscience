import { Suspense, lazy, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ActionIcon, Card, Group, Loader, Modal, SegmentedControl, Text, Tooltip } from "@mantine/core";
import { api } from "../api";
import { stageColor, stageFill } from "./graphFlow";
import { clearPositions } from "./graphPositions";
import type { LayoutMode } from "./LineageGraph";

const LineageGraph = lazy(() => import("./LineageGraph"));

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

function Swatch({ color, fill, label }: { color: string; fill: string; label: string }) {
  return (
    <Group gap={4} wrap="nowrap">
      <span style={{ width: 10, height: 10, borderRadius: 3, border: `2px solid ${color}`, background: fill }} />
      <Text size="xs" c="dimmed">{label}</Text>
    </Group>
  );
}

function Line({ dashed, label }: { dashed: boolean; label: string }) {
  return (
    <Group gap={4} wrap="nowrap">
      <span style={{ width: 16, borderTop: `2px ${dashed ? "dashed" : "solid"} #8a8f98` }} />
      <Text size="xs" c="dimmed">{label}</Text>
    </Group>
  );
}

function Legend() {
  return (
    <Group gap="md" mt="xs" wrap="wrap">
      <Swatch color={stageColor("idea")} fill={stageFill("idea")} label="idea" />
      <Swatch color={stageColor("experiment")} fill={stageFill("experiment")} label="experiment" />
      <Swatch color={stageColor("result")} fill={stageFill("result")} label="result" />
      <Line dashed={false} label="lineage" />
      <Line dashed label="evidential" />
    </Group>
  );
}

export default function LineageCard({ programId }: { programId: string }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<LayoutMode>("box");
  const [resetKey, setResetKey] = useState(0);   // bump to remount the graph after reset
  const graph = useQuery({ queryKey: ["graph", programId], queryFn: () => api.getGraph(programId) });

  const resetLayout = () => { clearPositions(programId); setResetKey((k) => k + 1); };

  const controls = (
    <Group gap="xs">
      <SegmentedControl
        size="xs"
        value={mode}
        onChange={(v) => setMode(v as LayoutMode)}
        data={[{ label: "box", value: "box" }, { label: "dot", value: "dot" }]}
      />
      <Tooltip label="Reset layout">
        <ActionIcon variant="subtle" onClick={resetLayout} aria-label="Reset layout">↺</ActionIcon>
      </Tooltip>
    </Group>
  );

  const go = (nodeId: string) => {
    const n = graph.data?.nodes.find((x) => x.id === nodeId);
    if (!n) return;
    if (n.kind === "idea") nav(`/programs/${programId}/ideas`);
    else nav(`/sprints/${nodeId}`);
  };

  const hasGraph = !!graph.data && graph.data.nodes.length > 0;

  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" mb="xs">
        <Text fw={600}>lineage</Text>
        {hasGraph && (
          <Group gap="xs">
            {controls}
            <Tooltip label="Expand">
              <ActionIcon variant="subtle" onClick={() => setOpen(true)} aria-label="Expand graph">⛶</ActionIcon>
            </Tooltip>
          </Group>
        )}
      </Group>

      {graph.isError ? (
        <Text c="dimmed" size="sm">Couldn't load the lineage graph. {String(graph.error)}</Text>
      ) : !graph.data ? (
        <Loader size="sm" />
      ) : !hasGraph ? (
        <Text c="dimmed" size="sm">
          No lineage yet — the PM records edges (inspired_by, builds_on, confirms/refutes)
          as the program develops.
        </Text>
      ) : (
        <>
          <div style={{ height: 320 }}>
            <Suspense fallback={<Loader size="sm" />}>
              <LineageGraph key={`inline-${resetKey}`} graph={graph.data} programId={programId} mode={mode} onNodeClick={go} />
            </Suspense>
          </div>
          <Legend />
        </>
      )}

      <Modal opened={open} onClose={() => setOpen(false)} fullScreen title="Program lineage">
        {hasGraph && (
          <>
            <Group justify="flex-end" mb="xs">{controls}</Group>
            <div style={{ height: "80vh" }}>
              <Suspense fallback={<Loader size="sm" />}>
                <LineageGraph key={`modal-${resetKey}`} graph={graph.data!} programId={programId} mode={mode} onNodeClick={(id) => { setOpen(false); go(id); }} />
              </Suspense>
            </div>
            <Legend />
          </>
        )}
      </Modal>
    </Card>
  );
}
