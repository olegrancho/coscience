import { Button, Card, Code, Group, Loader, SimpleGrid, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { availableActions, type SprintStatus } from "../sprintActions";
import { EmptyState, StatusBadge } from "../components/ui";
import SprintEditModal from "../components/SprintEditModal";

function programOf(id: string) {
  const i = id.indexOf("-");
  return i === -1 ? id : id.slice(0, i);
}

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function SprintDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const sprint = useQuery({ queryKey: ["sprint", id], queryFn: () => api.getSprint(id) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["sprint", id] });

  if (sprint.isLoading) return <Loader color="machine" />;
  if (sprint.error || !sprint.data) {
    return <EmptyState title="Experiment not found">Nothing here at “{id}”. It may have been removed.</EmptyState>;
  }
  const s = sprint.data;
  const actions = availableActions(s.status as SprintStatus);
  const prog = programOf(s.id);
  const resources = Object.entries(s.resources_required);

  const approve = async () => {
    try { await api.approveSprint(id); notifications.show({ color: "teal", title: "Approved", message: "It'll run when compute is free." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't approve", message: String(e) }); }
  };
  const reject = async () => {
    try { await api.rejectSprint(id); notifications.show({ color: "gray", title: "Rejected", message: "Canceled — it won't run." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't reject", message: String(e) }); }
  };

  return (
    <Stack gap="lg">
      <div>
        <div className="eyebrow" style={{ marginBottom: 7 }}>
          experiment · for <Link to={`/programs/${prog}`} style={{ color: "var(--machine)", textDecoration: "none" }}>{prog}</Link>
        </div>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0, maxWidth: 620, lineHeight: 1.25 }}>
            {s.goals || s.id}
          </h1>
          <Group gap={8} wrap="nowrap">
            {actions.includes("approve") && <Button color="signal" onClick={approve}>Approve</Button>}
            {actions.includes("reject") && <Button variant="default" onClick={reject}>Reject</Button>}
            {actions.includes("edit") && <Button variant="light" color="machine" onClick={() => setEditing(true)}>Edit</Button>}
          </Group>
        </Group>
        <Group gap={10} mt={9}>
          <StatusBadge status={s.status} />
          <span className="mono" style={{ fontSize: 12, color: "var(--ink-faint)" }}>ref {s.id}</span>
        </Group>
      </div>

      {s.rationale && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 8 }}>why the AI proposed this</div>
          <Text>{s.rationale}</Text>
        </Card>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>at a glance</div>
          <Stack gap={7}>
            <Group justify="space-between"><Text size="sm" c="dimmed">Priority</Text><Text size="sm" className="mono">{s.priority}</Text></Group>
            <Group justify="space-between"><Text size="sm" c="dimmed">Preemptible</Text><Text size="sm">{s.preemptible ? "yes" : "no"}</Text></Group>
            <Group justify="space-between"><Text size="sm" c="dimmed">Running now</Text><Text size="sm">{s.lease ? "yes — holds a lease" : "no"}</Text></Group>
          </Stack>
        </Card>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>compute it will use</div>
          {resources.length ? (
            <Stack gap={6}>
              {resources.map(([k, v]) => (
                <Group key={k} justify="space-between"><Text size="sm" c="dimmed">{k}</Text><Text size="sm" className="mono">{v}</Text></Group>
              ))}
            </Stack>
          ) : <Text size="sm" c="dimmed">Minimal — no reserved resources.</Text>}
        </Card>
      </SimpleGrid>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>plan · {s.plan.length} {s.plan.length === 1 ? "step" : "steps"}</div>
        <Table>
          <Table.Tbody>
            {s.plan.map((step) => {
              const done = s.completed_steps.includes(step.id);
              return (
                <Table.Tr key={step.id}>
                  <Table.Td style={{ width: 28, color: done ? "var(--st-done)" : "var(--ink-faint)" }}>{done ? "✓" : "○"}</Table.Td>
                  <Table.Td style={{ width: 90 }}><span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>{step.id}</span></Table.Td>
                  <Table.Td><Code>{step.run}</Code></Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Card>

      <SprintEditModal sprint={s} opened={editing} onClose={() => setEditing(false)} onDone={refresh} />
    </Stack>
  );
}
