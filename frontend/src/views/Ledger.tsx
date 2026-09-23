import { Button, Card, Group, Loader, Stack, Table, Text, Tooltip } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import CallLog from "../components/CallLog";
import HostsCard from "../components/HostsCard";
import { EmptyState, Gauge, UsagePanel, formatDuration, gaugeUsers } from "../components/ui";
import { fullTime } from "../components/timefmt";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };
const WORKER_KEY = "workers";
const HOUSEKEEPER_KEY = "housekeepers";
// The limits a gauge may step (G2): a machine's own amounts are set on its card.
const PLATFORM_KEYS = new Set([WORKER_KEY, HOUSEKEEPER_KEY]);
/** One save per adjustment, not one per click — each save is also a substrate commit. */
const SAVE_DEBOUNCE_MS = 1000;

export default function Ledger() {
  const qc = useQueryClient();
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.getUsage });
  // Locally adjusted capacities, layered over the server's. The 10s ledger poll
  // would otherwise snap a half-finished adjustment back mid-click.
  const [pending, setPending] = useState<Record<string, number>>({});
  const [saveError, setSaveError] = useState("");
  const pendingRef = useRef(pending);
  const capacityRef = useRef<Record<string, number>>({});
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  pendingRef.current = pending;
  if (ledger.data) capacityRef.current = ledger.data.local_capacity ?? ledger.data.capacity;

  const save = useCallback(async () => {
    timerRef.current = null;
    const edits = pendingRef.current;
    if (!Object.keys(edits).length) return;
    try {
      // Only the platform limits have steppers (G2), so this never writes a machine.
      await api.setPlatformLimits(edits);
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

  // A cap that does not exist has no gauge, so no stepper: this starts one at what is
  // running now (at least 1), and the gauge's steppers take it from there.
  const addLimit = async (key: string) => {
    setSaveError("");
    try {
      await api.setPlatformLimits({ [key]: Math.max(1, ledger.data?.used[key] ?? 0) });
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setSaveError(String(e));
    }
  };

  // ∞ on a limit's gauge: no cap at all. Any stepper adjustment still waiting to be
  // saved for it is dropped first, so the debounce cannot put the cap straight back.
  const removeLimit = async (key: string) => {
    setSaveError("");
    setPending((prev) => {
      const { [key]: _dropped, ...rest } = prev;
      pendingRef.current = rest;
      return rest;
    });
    try {
      await api.setPlatformLimits({ [key]: null });
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setSaveError(String(e));
    }
  };

  const togglePause = async () => {
    setSaveError("");
    try {
      await api.setPause(!ledger.data?.paused);
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setSaveError(String(e));
    }
  };

  if (ledger.isLoading) return <Loader color="machine" />;
  if (ledger.error || !ledger.data) return <EmptyState title="Couldn't load compute">Try again in a moment.</EmptyState>;
  const l = ledger.data;
  const keys = Object.keys(l.capacity);
  // Once a remote server takes work the pool totals include it; the capacity editor
  // and the steppers change this machine's own amounts.
  const editable = l.local_capacity ?? l.capacity;
  const remoteTakesWork = (l.hosts ?? []).some((h) => h.placeable && h.ssh);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>resources</div>
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>Compute</h1>
        </div>
        <Group gap="sm">
          {l.paused && (
            <Text size="sm" c="dimmed">
              {l.leases.length
                ? `Paused — ${l.leases.length} still finishing`
                : "Paused — nothing running"}
            </Text>
          )}
          <Button size="xs" color={l.paused ? "green" : "red"}
                  variant={l.paused ? "filled" : "default"}
                  onClick={() => { void togglePause(); }}>
            {l.paused ? "Resume" : "Pause"}
          </Button>
        </Group>
      </Group>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 16 }}>Claude usage</div>
        {usage.data ? <UsagePanel usage={usage.data} />
          : <Text size="sm" c="dimmed">Usage reading unavailable.</Text>}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 16 }}>capacity in use</div>

        {!(WORKER_KEY in l.capacity) && (
          <Text size="sm" c="dimmed" style={{ marginBottom: 16 }}>
            No worker cap — any number of agents can run at once.{" "}
            <button type="button" className="linklike" onClick={() => { void addLimit(WORKER_KEY); }}>
              Set a limit
            </button>
          </Text>
        )}

        {!(HOUSEKEEPER_KEY in l.capacity) && (
          <Text size="sm" c="dimmed" style={{ marginBottom: 16 }}>
            No housekeeping cap — PM and wiki agents start whenever they are due,
            however many are already running.{" "}
            <button type="button" className="linklike" onClick={() => { void addLimit(HOUSEKEEPER_KEY); }}>
              Set a limit
            </button>
          </Text>
        )}

        <Text size="sm" c="dimmed" style={{ marginBottom: 16 }}>
          {remoteTakesWork ? "Totals include remote servers. " : ""}
          A machine's CPUs, memory and cards are set on its own row under servers.
        </Text>

        {keys.length ? (
          <Stack gap={16}>
            {keys.map((k) => (
              <Gauge key={k} label={k} used={l.used[k] ?? 0}
                     capacity={pending[k] ?? l.capacity[k]}
                     pending={k in pending}
                     onAdjust={PLATFORM_KEYS.has(k) ? (delta) => adjust(k, delta) : undefined}
                     onUnlimited={PLATFORM_KEYS.has(k) ? () => { void removeLimit(k); } : undefined}
                     users={gaugeUsers(l.leases, k)} />
            ))}
          </Stack>
        ) : <Text size="sm" c="dimmed">No compute pool is configured yet, so there's nothing to meter.</Text>}

        {saveError && <Text size="sm" c="red" style={{ marginTop: 12 }}>{saveError}</Text>}
      </Card>

      <HostsCard hosts={l.hosts ?? []} errors={l.host_errors ?? []} stranded={l.stranded ?? []}
                 localCapacity={editable} />

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>running now · {l.leases.length}</div>
        {l.leases.length === 0 ? (
          <Text size="sm" c="dimmed">Nothing is running right now. Approved experiments show up here while they use compute.</Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Experiment</Table.Th><Table.Th>Running for</Table.Th><Table.Th>Using</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {l.leases.map((x, i) => {
                return (
                  <Table.Tr key={i}>
                    {/* The title, like every other list; the id only when there is none. */}
                    <Table.Td><Link to={`/sprints/${x.sprint_id}`} className={x.title ? undefined : "mono"} style={{ fontSize: 13, color: "var(--machine)", textDecoration: "none" }}>{x.title || x.sprint_id}</Link></Table.Td>
                    <Table.Td className="mono" style={{ fontSize: 13 }}>
                      {x.granted_at ? (
                        <Tooltip label={`compute granted ${fullTime(x.granted_at)}`} withArrow>
                          <span>{formatDuration(Date.now() / 1000 - x.granted_at)}</span>
                        </Tooltip>
                      ) : "—"}
                    </Table.Td>
                    <Table.Td className="mono" style={{ fontSize: 13 }}>{`${Object.entries(x.amounts).map(([k, v]) => `${v} ${k}`).join(", ")}${x.host && x.host !== "local" ? ` on ${x.host}` : ""}`}</Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>claude calls</div>
        <Text size="xs" c="dimmed" style={{ marginBottom: 16 }}>
          Every call the platform has made on this host — PM, workers, wiki and chat.
        </Text>
        <CallLog />
      </Card>

    </Stack>
  );
}
