import { Card, Group, Stack, Text, Tooltip } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { experimentLineage, type LineageLink } from "../experimentLineage";
import { StatusBadge } from "./ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

function Row({ programId, l }: { programId: string; l: LineageLink }) {
  const to = l.node.kind === "idea" ? `/programs/${programId}/ideas#${l.node.id}` : `/sprints/${l.node.id}`;
  const verb = (
    <Text size="xs" c="dimmed" className="mono" style={{ width: 150, flexShrink: 0 }}>{l.verb}</Text>
  );
  return (
    <Group gap={10} wrap="nowrap" align="baseline">
      {l.rationale ? <Tooltip label={l.rationale} multiline w={360} withArrow>{verb}</Tooltip> : verb}
      <Link to={to} className="view" style={{ flex: 1, minWidth: 0 }}>
        {l.node.kind === "idea" ? `idea: ${l.node.label}` : l.node.label || l.node.id}
      </Link>
      {l.node.status && <StatusBadge status={l.node.status} />}
    </Group>
  );
}

/** Below an experiment's results (N1): where it came from and what followed it,
 *  read from the program's lineage graph each time the page loads. */
export default function ExperimentLineageCard({ programId, sprintId }: { programId: string; sprintId: string }) {
  const graph = useQuery({ queryKey: ["graph", programId], queryFn: () => api.getGraph(programId),
                           enabled: !!programId });
  const { from, followed } = experimentLineage(graph.data, sprintId);
  if (!from.length && !followed.length) return null;
  return (
    <Card id="sec-lineage" padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" align="baseline" mb={10}>
        <div className="eyebrow">lineage</div>
        <Link to={`/programs/${programId}#sec-lineage`} className="view" style={{ fontSize: 13 }}>on the graph →</Link>
      </Group>
      <Stack gap={14}>
        {from.length > 0 && (
          <Stack gap={6}>
            <Text size="sm" fw={600}>Where it came from</Text>
            {from.map((l) => <Row key={l.edgeId} programId={programId} l={l} />)}
          </Stack>
        )}
        {followed.length > 0 && (
          <Stack gap={6}>
            <Text size="sm" fw={600}>What followed</Text>
            {followed.map((l) => <Row key={l.edgeId} programId={programId} l={l} />)}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
