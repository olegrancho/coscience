import { Button, Card, Group, Table, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import type { LedgerHost, StrandedLease } from "../api";
import AddHostModal from "./AddHostModal";
import { accessLabel } from "./programAccess";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/** How a run directory found on the server is labelled (O20). The list comes from
 *  the server itself, so it can hold a folder no sprint record explains. */
export function leftoverLabel(status: string): string {
  return status === "unknown" ? "no sprint record" : status;
}

const DRAIN_TEXT = "takes no new work by choice; what is already running finishes";

/** What a removing server is still waiting on before it can leave the pool. */
function removingDetail(host: LedgerHost): string {
  if (!host.waiting_on.length) return "leaves the pool on the dispatcher's next cycle";
  return `waiting on ${host.waiting_on.map((w) => `${w.sprint_id} (${w.status}, ${w.reason})`).join(", ")}`;
}

function notAnsweringSince(host: LedgerHost): string {
  const h = host.health;
  if (!h) return "";
  const hhmm = new Date(h.fail_since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `not answering since ${hhmm} — ${h.reason}`;
}

export type HostStatusKey = "working" | "ready" | "draining" | "removing" | "offline" | "unchecked";

export interface HostStatus {
  key: HostStatusKey;
  mark: string;      // the dot, so the column reads at a glance
  color: string;
  detail: string;    // the whole truth, on hover — every fact the word folds up
}

const MARKS: Record<HostStatusKey, { mark: string; color: string }> = {
  working: { mark: "●", color: "var(--machine)" },
  ready: { mark: "○", color: "var(--ink-muted)" },
  draining: { mark: "◐", color: "var(--signal)" },
  removing: { mark: "◑", color: "var(--signal)" },
  offline: { mark: "✕", color: "#c0392b" },
  unchecked: { mark: "·", color: "var(--ink-faint)" },
};

/** One word for what a server is doing, chosen from the facts in priority order:
 *  a human's decision outranks how the server is answering, which outranks whether
 *  work happens to be on it. The word never carries a second fact — that is what
 *  `detail` is for, so the column stays scannable (O12). */
export function hostStatus(host: LedgerHost): HostStatus {
  const remoteOff = !!host.ssh && !host.placeable;
  const state = host.health?.state ?? (host.ssh ? "unchecked" : "local");
  const health = remoteOff ? "remote placement is off for this deployment"
    : state === "local" ? "this machine"
    : state === "ok" ? "answering health checks"
    : state === "unchecked" ? "not checked yet"
    : notAnsweringSince(host);
  const lines = (...parts: string[]) => parts.filter(Boolean).join("; ");

  let key: HostStatusKey;
  let detail: string;
  if (host.removing) {
    key = "removing";
    detail = lines(removingDetail(host), health);
  } else if (host.drain) {
    key = "draining";
    detail = lines(DRAIN_TEXT, health);
  } else if (state === "quiet" || state === "failing") {
    key = "offline";
    // Quiet is the one that also changes placement, so it says so; failing is
    // still trusted with new work until QUIET_AFTER passes.
    detail = lines(health, state === "quiet" ? "takes no new work" : "");
  } else if (remoteOff || state === "unchecked") {
    key = "unchecked";
    detail = health;
  } else if ((host.leases ?? 0) > 0) {
    key = "working";
    const n = host.leases ?? 0;
    detail = lines(`${n} sprint${n === 1 ? "" : "s"} running here`, health);
  } else {
    key = "ready";
    detail = lines("nothing running", health);
  }
  return { key, ...MARKS[key], detail };
}

const MAX_PIPS = 32;
const num = (v: number) => (Number.isInteger(v) ? String(v) : String(Number(v.toFixed(1))));

export interface Slots { pips: ("full" | "empty")[]; label: string; per: number }

/** Slots for a plain amount: one pip per unit, so 24 cores really are 24 squares.
 *  Past MAX_PIPS one pip stands for several units — a 128-core server would
 *  otherwise draw a row nobody can count anyway. `per` says which it is. */
export function slots(total: number, used: number): Slots {
  if (!total || total <= 0) return { pips: [], label: "—", per: 1 };
  const per = Math.ceil(total / MAX_PIPS);
  const count = Math.ceil(total / per);
  const filled = Math.min(count, Math.ceil(Math.max(used, 0) / per));
  const pips: ("full" | "empty")[] = Array.from(
    { length: count }, (_, i) => (i < filled ? "full" : "empty"));
  return { pips, label: `${num(used)}/${num(total)}`, per };
}

export interface CardSlot { state: "full" | "part" | "empty"; title: string }

/** One pip per GPU card: whole cards in use are filled, a card lent out in shares
 *  is half filled, and a free card is empty. */
export function cardSlots(host: LedgerHost): CardSlot[] {
  return host.gpus.map((g) => {
    const what = `GPU ${g.index}${g.model ? ` — ${g.model}` : ""}${g.vram_gb ? `, ${num(g.vram_gb)} GB` : ""}`;
    if (g.whole) return { state: "full" as const, title: `${what}: in use` };
    if (g.shared_gb > 0) {
      return { state: "part" as const, title: `${what}: ${num(g.shared_gb)} GB lent out in shares` };
    }
    return { state: "empty" as const, title: `${what}: free` };
  });
}

const PIP = { width: 7, height: 11, borderRadius: 1, display: "inline-block" } as const;
const PIP_FILL: Record<string, string> = {
  full: "var(--machine)",
  part: "linear-gradient(to top, var(--machine) 50%, var(--hairline) 50%)",
  empty: "var(--hairline)",
};

function Pips({ states, title }: { states: string[]; title: string }) {
  if (!states.length) return <Text size="xs" c="dimmed">—</Text>;
  return (
    <span style={{ display: "inline-flex", gap: 2, alignItems: "center" }} title={title}>
      {states.map((s, i) => (
        <span key={i} style={{ ...PIP, background: PIP_FILL[s] }} />
      ))}
    </span>
  );
}

export default function HostsCard(
  { hosts, errors, stranded = [], localCapacity }: {
    hosts: LedgerHost[]; errors: string[]; stranded?: StrandedLease[];
    localCapacity?: Record<string, number>;
  },
) {
  const [adding, setAdding] = useState(false);
  const [configuring, setConfiguring] = useState<LedgerHost | null>(null);
  const qc = useQueryClient();

  const open = (host: LedgerHost) => setConfiguring(host);

  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" style={{ marginBottom: 12 }}>
        <div className="eyebrow">servers · {hosts.length}</div>
        <Button size="xs" variant="default" onClick={() => setAdding(true)}>Add server</Button>
      </Group>
      <Table fz="xs" verticalSpacing={6}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Server</Table.Th><Table.Th>CPU</Table.Th><Table.Th>GPU</Table.Th>
            <Table.Th>Programs</Table.Th><Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {hosts.map((h) => {
            const status = hostStatus(h);
            const cpu = slots(h.capacity.cpu ?? 0, h.used?.cpu ?? 0);
            const mem = "memory_gb" in h.capacity
              ? slots(h.capacity.memory_gb, h.used?.memory_gb ?? 0) : null;
            const cards = cardSlots(h);
            return (
              <Fragment key={h.name}>
                <Table.Tr role="button" tabIndex={0} aria-label={`Configure ${h.name}`}
                          style={{ cursor: "pointer" }}
                          onClick={() => open(h)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(h); }
                          }}>
                  <Table.Td className="mono">{h.name}</Table.Td>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Pips states={cpu.pips}
                            title={cpu.per > 1 ? `${cpu.label} cores — one square is ${cpu.per} cores`
                                               : `${cpu.label} cores in use`} />
                      <Text size="xs" c="dimmed">{cpu.label}</Text>
                    </Group>
                    {mem && <Text size="xs" c="dimmed">{mem.label} GB memory</Text>}
                  </Table.Td>
                  <Table.Td>
                    <Pips states={cards.map((c) => c.state)}
                          title={cards.map((c) => c.title).join("\n")} />
                  </Table.Td>
                  <Table.Td>{accessLabel(h)}</Table.Td>
                  <Table.Td>
                    <span title={status.detail} style={{ whiteSpace: "nowrap" }}>
                      <span style={{ color: status.color, marginRight: 6 }}>{status.mark}</span>
                      {status.key}
                    </span>
                  </Table.Td>
                </Table.Tr>
                {h.leftover && h.leftover.length > 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={5}>
                      <Text size="xs" c="dimmed">
                        On the server now, with no sprint still using it (remove by hand):{" "}
                        {h.leftover.map((l) => `${l.path} (${leftoverLabel(l.status)})`).join(", ")}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Fragment>
            );
          })}
        </Table.Tbody>
      </Table>
      {errors.map((e, i) => (
        <Text key={`${i}-${e}`} size="xs" c="red" style={{ marginTop: 8 }}>{e}</Text>
      ))}
      {stranded.map((s, i) => (
        <Text key={i} size="xs" c="red" style={{ marginTop: 4 }}>
          {s.listed
            ? `${s.sprint_id} still holds a lease on ${s.host}, which takes no work while remote placement is off.`
            : `${s.sprint_id} still holds a lease on ${s.host}, which is no longer in the pool: stop the sprint or add the server back.`}
        </Text>
      ))}
      <AddHostModal
        opened={adding || !!configuring}
        onClose={() => {
          setAdding(false); setConfiguring(null);
          qc.invalidateQueries({ queryKey: ["ledger"] });
        }}
        host={configuring ?? undefined}
        local={!!configuring && !configuring.ssh}
        localCapacity={localCapacity}
      />
    </Card>
  );
}
