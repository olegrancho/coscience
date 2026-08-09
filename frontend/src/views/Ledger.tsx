import { Button, Card, Group, Loader, Stack, Table, Text } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import CapacityModal from "../components/CapacityModal";
import { EmptyState, Gauge, UsagePanel } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };
const WORKER_KEY = "workers";
/** One save per adjustment, not one per click — each save is also a substrate commit. */
const SAVE_DEBOUNCE_MS = 1000;

export default function Ledger() {
  const qc = useQueryClient();
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.getUsage });
  const [editing, setEditing] = useState(false);
  // Locally adjusted capacities, layered over the server's. The 10s ledger poll
  // would otherwise snap a half-finished adjustment back mid-click.
  const [pending, setPending] = useState<Record<string, number>>({});
  const [saveError, setSaveError] = useState("");
  const pendingRef = useRef(pending);
  const capacityRef = useRef<Record<string, number>>({});
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  pendingRef.current = pending;
  if (ledger.data) capacityRef.current = ledger.data.capacity;

  const save = useCallback(async () => {
    timerRef.current = null;
    const edits = pendingRef.current;
    if (!Object.keys(edits).length) return;
    try {
      await api.setCapacity({ ...capacityRef.current, ...edits });
      setPending({});                    // server now agrees; drop the overlay
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setPending({});                    // revert: never show a number the server rejected
      setSaveError(String(e));
    }
  }, [qc]);

  // A pending adjustment shouldn't die with the page.
  useEffect(() => () => { if (timerRef.current) void save(); }, [save]);

  const adjust = (key: string, delta: number) => {
    setSaveError("");
    setPending((prev) => {
      const from = prev[key] ?? capacityRef.current[key] ?? 0;
      return { ...prev, [key]: Math.max(0, from + delta) };
    });
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => { void save(); }, SAVE_DEBOUNCE_MS);
  };

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
        <Group justify="space-between" style={{ marginBottom: 16 }}>
          <div className="eyebrow">capacity in use</div>
          <Button size="xs" variant="default" onClick={() => setEditing(true)}>Edit capacity</Button>
        </Group>

        {!(WORKER_KEY in l.capacity) && (
          <Text size="sm" c="dimmed" style={{ marginBottom: 16 }}>
            No worker cap — any number of agents can run at once. Add a{" "}
            <code>{WORKER_KEY}</code> limit to bound it.
          </Text>
        )}

        {keys.length ? (
          <Stack gap={16}>
            {keys.map((k) => (
              <Gauge key={k} label={k} used={l.used[k] ?? 0}
                     capacity={pending[k] ?? l.capacity[k]}
                     pending={k in pending}
                     onAdjust={(delta) => adjust(k, delta)} />
            ))}
          </Stack>
        ) : <Text size="sm" c="dimmed">No compute pool is configured yet, so there's nothing to meter.</Text>}

        {saveError && <Text size="sm" c="red" style={{ marginTop: 12 }}>{saveError}</Text>}
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

      <CapacityModal opened={editing} onClose={() => setEditing(false)}
                     capacity={l.capacity} used={l.used} />
    </Stack>
  );
}
