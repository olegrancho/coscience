import { Button, Card, Group, Table, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api } from "../api";
import type { LedgerHost, StrandedLease } from "../api";
import AddHostModal from "./AddHostModal";
import { accessLabel } from "./programAccess";

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

const DRAIN_SUFFIX = "draining — takes no new work";

/** What a removing server's status line ends with: what it is still waiting on,
 *  if anything, before it can leave the pool. */
function removingSuffix(host: LedgerHost): string {
  if (!host.waiting_on.length) return "removing — leaves the pool on the dispatcher's next cycle";
  return `removing — waiting on ${host.waiting_on.map((w) => `${w.sprint_id} (${w.status}, ${w.reason})`).join(", ")}`;
}

/** How a server is answering health checks, in words. Falls back to the old
 *  placeable-based text against a backend that doesn't send `health` yet. */
export function healthText(host: LedgerHost): string {
  // Fix C: while remote placement itself is off, this host is never checked — the
  // service always sends "unchecked" for it, which would otherwise misleadingly
  // read as "not checked yet" (as if it's about to be). Say why plainly instead,
  // without even looking at the (meaningless) health state.
  if (host.ssh && !host.placeable) {
    if (host.removing) return `waits for remote launch; ${removingSuffix(host)}`;
    return host.drain ? `waits for remote launch; ${DRAIN_SUFFIX}` : "waits for remote launch";
  }
  if (!host.health) return "takes work";
  const { state, fail_since, reason } = host.health;
  const hhmm = new Date(fail_since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const byState: Record<typeof state, string> = {
    local: "takes work",
    unchecked: "not checked yet",
    ok: "answering",
    failing: `not answering since ${hhmm} — ${reason}`,
    quiet: `not answering since ${hhmm} — ${reason}`,
  };
  let text = byState[state];
  // M8b: "takes no new work" is said once — as the drain suffix when draining,
  // otherwise as quiet's own reason.
  if (state === "quiet" && !host.drain && !host.removing) text += "; takes no new work";
  if (host.removing) return state === "ok" ? removingSuffix(host) : `${text}; ${removingSuffix(host)}`;
  if (host.drain) return state === "ok" ? DRAIN_SUFFIX : `${text}; ${DRAIN_SUFFIX}`;
  return text;
}

/** What is in use on a server, one line per resource: cores, memory, each card. */
export function inUseText(host: LedgerHost): string[] {
  const used = host.used ?? {};
  const parts: string[] = [];
  if ("cpu" in host.capacity) parts.push(`${used.cpu ?? 0} of ${host.capacity.cpu} CPU cores`);
  if ("memory_gb" in host.capacity) parts.push(`${used.memory_gb ?? 0} of ${host.capacity.memory_gb} GB memory`);
  host.gpus.forEach((g) => {
    if (g.whole) parts.push(`GPU ${g.index}: in use`);
    else if (g.shared_gb > 0) parts.push(`GPU ${g.index}: ${g.shared_gb} of ${g.vram_gb} GB shared`);
    else parts.push(`GPU ${g.index}: free`);
  });
  return parts;
}

export default function HostsCard(
  { hosts, errors, stranded = [], localCapacity }: {
    hosts: LedgerHost[]; errors: string[]; stranded?: StrandedLease[];
    localCapacity?: Record<string, number>;
  },
) {
  const [adding, setAdding] = useState(false);
  const [configuring, setConfiguring] = useState<LedgerHost | null>(null);
  const [error, setError] = useState("");
  const qc = useQueryClient();

  const keep = async (name: string) => {
    setError("");
    try {
      await api.keepHost(name);
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (host: LedgerHost) => {
    const leftover = host.leftover ?? [];
    // M5: name what stays behind, so the confirm prompt isn't a leap of faith.
    const extra = leftover.length
      ? ` It leaves behind: ${leftover.slice(0, 5).map((l) => l.path).join(", ")}` +
        (leftover.length > 5 ? ` and ${leftover.length - 5} more.` : ".")
      : "";
    if (!window.confirm(
      `Remove ${host.name}? It takes no new work now and leaves the pool as soon as nothing runs there. `
      + `Its run directories stay on the server.${extra}`,
    )) return;
    setError("");
    try {
      await api.removeHost(host.name);
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setError(String(e));
    }
  };

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
            <Table.Th>In use</Table.Th>
            <Table.Th>Programs</Table.Th><Table.Th>Status</Table.Th><Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {hosts.map((h) => (
            <Fragment key={h.name}>
              <Table.Tr>
                <Table.Td className="mono">{h.name}</Table.Td>
                <Table.Td className="mono">{h.ssh || "this machine"}</Table.Td>
                <Table.Td>{hostOffer(h)}</Table.Td>
                <Table.Td>
                  {inUseText(h).map((line, i) => <Text key={i} size="sm">{line}</Text>)}
                </Table.Td>
                <Table.Td>{accessLabel(h)}</Table.Td>
                <Table.Td>{healthText(h)}</Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Button size="xs" variant="default"
                            aria-label={`Configure ${h.name}`}
                            onClick={() => setConfiguring(h)}>
                      Config
                    </Button>
                    {h.ssh && (h.removing || h.drain ? (
                      <Button size="xs" variant="default"
                              aria-label={`Keep ${h.name}`}
                              onClick={() => void keep(h.name)}>
                        Keep
                      </Button>
                    ) : (
                      <Button size="xs" variant="default" color="red"
                              aria-label={`Remove ${h.name}`}
                              onClick={() => void remove(h)}>
                        Remove
                      </Button>
                    ))}
                  </Group>
                </Table.Td>
              </Table.Tr>
              {h.leftover && h.leftover.length > 0 && (
                <Table.Tr>
                  <Table.Td colSpan={7}>
                    <Text size="sm" c="dimmed">
                      Left on the server by finished sprints (remove by hand):{" "}
                      {h.leftover.map((l) => `${l.path} (${l.status})`).join(", ")}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Fragment>
          ))}
        </Table.Tbody>
      </Table>
      {errors.map((e, i) => (
        <Text key={`${i}-${e}`} size="sm" c="red" style={{ marginTop: 8 }}>{e}</Text>
      ))}
      {error && <Text size="sm" c="red" style={{ marginTop: 8 }}>{error}</Text>}
      {stranded.map((s, i) => (
        <Text key={i} size="sm" c="red" style={{ marginTop: 4 }}>
          {s.listed
            ? `${s.sprint_id} still holds a lease on ${s.host}, which takes no work while remote placement is off.`
            : `${s.sprint_id} still holds a lease on ${s.host}, which is no longer in the pool: stop the sprint or add the server back.`}
        </Text>
      ))}
      <AddHostModal
        opened={adding || !!configuring}
        onClose={() => { setAdding(false); setConfiguring(null); }}
        host={configuring ?? undefined}
        local={!!configuring && !configuring.ssh}
        localCapacity={localCapacity}
      />
    </Card>
  );
}
