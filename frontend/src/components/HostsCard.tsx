import { Button, Card, Group, Table, Text } from "@mantine/core";
import { useState } from "react";
import type { LedgerHost } from "../api";
import AddHostModal from "./AddHostModal";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/** What a server offers the pool, in words: cores, memory, then each card. */
export function hostOffer(host: LedgerHost): string {
  const parts: string[] = [];
  if (host.capacity.cpu) parts.push(`${host.capacity.cpu} CPU cores`);
  if (host.capacity.memory_gb) parts.push(`${host.capacity.memory_gb} GB memory`);
  if (host.gpus.length) {
    parts.push(host.gpus.map((g) => (g.vram_gb ? `${g.vram_gb} GB GPU` : "GPU (VRAM not declared)")).join(", "));
  }
  return parts.join(" · ") || "nothing declared";
}

export default function HostsCard({ hosts, errors }: { hosts: LedgerHost[]; errors: string[] }) {
  const [adding, setAdding] = useState(false);
  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" style={{ marginBottom: 12 }}>
        <div className="eyebrow">servers · {hosts.length}</div>
        <Button size="xs" variant="default" onClick={() => setAdding(true)}>Add server</Button>
      </Group>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Server</Table.Th><Table.Th>Reached by</Table.Th><Table.Th>Offers</Table.Th>
            <Table.Th>Programs</Table.Th><Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {hosts.map((h) => (
            <Table.Tr key={h.name}>
              <Table.Td className="mono">{h.name}</Table.Td>
              <Table.Td className="mono">{h.ssh || "this machine"}</Table.Td>
              <Table.Td>{hostOffer(h)}</Table.Td>
              <Table.Td>{h.programs.length ? h.programs.join(", ") : "all"}</Table.Td>
              <Table.Td>{h.placeable ? "takes work" : "waits for remote launch"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {errors.map((e, i) => (
        <Text key={`${i}-${e}`} size="sm" c="red" style={{ marginTop: 8 }}>{e}</Text>
      ))}
      <AddHostModal opened={adding} onClose={() => setAdding(false)} />
    </Card>
  );
}
