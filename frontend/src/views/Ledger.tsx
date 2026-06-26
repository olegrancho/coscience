import { Card, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function Ledger() {
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });
  if (ledger.isLoading) return <Loader />;
  if (ledger.error || !ledger.data) return <div>Failed to load ledger.</div>;
  const l = ledger.data;
  const keys = Object.keys(l.capacity);
  return (
    <Stack>
      <Title order={2}>Resources</Title>
      <Group>
        {keys.map((k) => (
          <Card withBorder key={k}>
            <Text fw={700}>{k}</Text>
            <Text size="sm">capacity {l.capacity[k]}</Text>
            <Text size="sm">used {l.used[k] ?? 0}</Text>
            <Text size="sm">available {l.available[k] ?? 0}</Text>
          </Card>
        ))}
      </Group>
      <Card withBorder>
        <Title order={4} mb="xs">Active leases</Title>
        <Table withTableBorder>
          <Table.Thead><Table.Tr><Table.Th>Lease</Table.Th><Table.Th>Sprint</Table.Th><Table.Th>Amounts</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {l.leases.map((lease, i) => {
              const x = lease as { id: string; sprint_id: string; amounts: unknown };
              return (
                <Table.Tr key={i}>
                  <Table.Td>{x.id}</Table.Td>
                  <Table.Td>{x.sprint_id}</Table.Td>
                  <Table.Td>{JSON.stringify(x.amounts)}</Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
