import { Badge, Button, Card, Code, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { availableActions, type SprintStatus } from "../sprintActions";
import SprintEditModal from "../components/SprintEditModal";

export default function SprintDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const sprint = useQuery({ queryKey: ["sprint", id], queryFn: () => api.getSprint(id) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["sprint", id] });

  if (sprint.isLoading) return <Loader />;
  if (sprint.error || !sprint.data) return <div>Sprint not found.</div>;
  const s = sprint.data;
  const actions = availableActions(s.status as SprintStatus);

  const approve = async () => { await api.approveSprint(id); refresh(); };
  const reject = async () => { await api.rejectSprint(id); refresh(); };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{s.id} <Badge ml="sm">{s.status}</Badge></Title>
        <Group>
          {actions.includes("approve") && <Button color="green" onClick={approve}>Approve</Button>}
          {actions.includes("reject") && <Button color="red" variant="light" onClick={reject}>Reject</Button>}
          {actions.includes("edit") && <Button variant="light" onClick={() => setEditing(true)}>Edit</Button>}
        </Group>
      </Group>

      <Card withBorder>
        <Text><b>Goals:</b> {s.goals}</Text>
        <Text><b>Priority:</b> {s.priority} &nbsp; <b>Preemptible:</b> {String(s.preemptible)}</Text>
        <Text><b>Resources:</b> {JSON.stringify(s.resources_required)}</Text>
        <Text><b>Lease:</b> {s.lease ? "held" : "none"}</Text>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">Plan</Title>
        <Table withTableBorder>
          <Table.Thead><Table.Tr><Table.Th>Step</Table.Th><Table.Th>Command</Table.Th><Table.Th>Done</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {s.plan.map((step) => (
              <Table.Tr key={step.id}>
                <Table.Td>{step.id}</Table.Td>
                <Table.Td><Code>{step.run}</Code></Table.Td>
                <Table.Td>{s.completed_steps.includes(step.id) ? "✓" : ""}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <SprintEditModal sprint={s} opened={editing}
                       onClose={() => setEditing(false)} onDone={refresh} />
    </Stack>
  );
}
