import { Card, Loader, Stack, Table, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EmptyState, Gauge, UsagePanel } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function Ledger() {
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.getUsage });
  if (ledger.isLoading) return <Loader color="machine" />;
  if (ledger.error || !ledger.data) return <EmptyState title="Couldn't load compute">Try again in a moment.</EmptyState>;
  const l = ledger.data;
  const keys = Object.keys(l.capacity);

  return (
    <Stack gap="lg">
      <div>
        <div className="eyebrow" style={{ marginBottom: 7 }}>resources</div>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>Compute</h1>
      </div>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 16 }}>Claude usage</div>
        {usage.data ? <UsagePanel usage={usage.data} />
          : <Text size="sm" c="dimmed">Usage reading unavailable.</Text>}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 16 }}>capacity in use</div>
        {keys.length ? (
          <Stack gap={16}>
            {keys.map((k) => <Gauge key={k} label={k} used={l.used[k] ?? 0} capacity={l.capacity[k]} />)}
          </Stack>
        ) : <Text size="sm" c="dimmed">No compute pool is configured yet, so there's nothing to meter.</Text>}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>running now · {l.leases.length}</div>
        {l.leases.length === 0 ? (
          <Text size="sm" c="dimmed">Nothing is running right now. Approved experiments show up here while they use compute.</Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Experiment</Table.Th><Table.Th>Using</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {l.leases.map((lease, i) => {
                const x = lease as { id: string; sprint_id: string; amounts: Record<string, number> };
                return (
                  <Table.Tr key={i}>
                    <Table.Td><Link to={`/sprints/${x.sprint_id}`} className="mono" style={{ fontSize: 13, color: "var(--machine)", textDecoration: "none" }}>{x.sprint_id}</Link></Table.Td>
                    <Table.Td className="mono" style={{ fontSize: 13 }}>{Object.entries(x.amounts).map(([k, v]) => `${v} ${k}`).join(", ")}</Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </Stack>
  );
}
