import {
  ActionIcon, Badge, Button, Card, Group, Loader, Stack, Table, Text,
  TextInput, Title,
} from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";
import ProposeSprintModal from "../components/ProposeSprintModal";

export default function ProgramDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [proposing, setProposing] = useState(false);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const guidance = useQuery({ queryKey: ["guidance", id], queryFn: () => api.listGuidance(id) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["program", id] });
    qc.invalidateQueries({ queryKey: ["guidance", id] });
  };

  if (program.isLoading) return <Loader />;
  if (program.error || !program.data) return <div>Program not found.</div>;
  const p = program.data;

  const setStatus = async (status: string) => { await api.setProgramStatus(id, status); refresh(); };
  const addNote = async () => { if (note.trim()) { await api.addGuidance(id, note.trim()); setNote(""); refresh(); } };
  const delNote = async (nid: string) => { await api.removeGuidance(id, nid); refresh(); };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{p.title || p.id} <Badge ml="sm">{p.status}</Badge></Title>
        <Group>
          <Text size="sm" c="dimmed">PM cycle {p.cycle}</Text>
          {p.status !== "active" && <Button variant="light" onClick={() => setStatus("active")}>Resume</Button>}
          {p.status === "active" && <Button variant="light" color="yellow" onClick={() => setStatus("paused")}>Pause</Button>}
          {p.status !== "closed" && <Button variant="light" color="gray" onClick={() => setStatus("closed")}>Close</Button>}
          <Button onClick={() => setProposing(true)}>Propose sprint</Button>
        </Group>
      </Group>

      <Card withBorder>
        <Title order={4} mb="xs">PM report</Title>
        <Markdown>{p.report || "_No report yet._"}</Markdown>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">Human guidance</Title>
        <Stack gap="xs">
          {(guidance.data ?? []).map((g) => (
            <Group key={g.id} justify="space-between">
              <Text>{g.text}</Text>
              <ActionIcon variant="subtle" color="red" onClick={() => delNote(g.id)}>✕</ActionIcon>
            </Group>
          ))}
          <Group>
            <TextInput style={{ flex: 1 }} placeholder="Add a steer for the PM…"
                       value={note} onChange={(e) => setNote(e.currentTarget.value)} />
            <Button onClick={addNote}>Add</Button>
          </Group>
        </Stack>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">Sprints</Title>
        <Table striped withTableBorder>
          <Table.Thead><Table.Tr>
            <Table.Th>Id</Table.Th><Table.Th>Status</Table.Th><Table.Th>Goals</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {p.sprints.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td><Link to={`/sprints/${s.id}`}>{s.id}</Link></Table.Td>
                <Table.Td><Badge>{s.status}</Badge></Table.Td>
                <Table.Td>{s.goals}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <ProposeSprintModal programId={id} opened={proposing}
                          onClose={() => setProposing(false)} onDone={refresh} />
    </Stack>
  );
}
