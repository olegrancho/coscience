import { Button, Card, Group, Table, Text, Tooltip } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type LedgerHost, type ProgramRow, type StrandedLease } from "../api";
import { describeDisk } from "./ui";
import AddHostModal from "./AddHostModal";

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

/** What to call a server on screen: its display name when someone set one, else
 *  the name it is filed under. The name itself never changes — leases, sprint
 *  records, probes and surveys are all keyed on it. */
export function hostLabel(host: LedgerHost): string {
  return (host.label ?? "").trim() || host.name;
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

export interface DiskCell { text: string; title: string; color?: string }

/** Free space for the table (B1). The figure is shown whenever the machine has
 *  reported one, not only once it is nearly gone: "how much room is left on the
 *  servers" is a question someone asks before there is a problem, and a warning
 *  that only appears at 2 GB cannot answer it. Colour is reserved for the two
 *  levels worth acting on, so a healthy pool stays quiet.
 *
 *  The reading rides along with the health check, so on a server that stopped
 *  answering it is as old as the last successful check — the hover says when,
 *  rather than presenting a stale number as current. */
export function diskCell(host: LedgerHost): DiskCell {
  const free = host.free_gb;
  if (free === null || free === undefined) {
    return {
      text: "—",
      title: host.ssh ? "this server has not reported its free space yet"
                      : "free space could not be read on this machine",
    };
  }
  const amount = free < 1 ? `${Math.round(free * 1024)} MB`
                          : `${free.toFixed(free < 10 ? 1 : 0)} GB`;
  const warning = describeDisk(free, host.disk);
  if (warning) {
    return {
      text: amount,
      color: host.disk === "critical" ? "var(--st-failed)" : "var(--st-queued)",
      title: warning,
    };
  }
  const at = host.ssh && host.health?.last_ok
    ? `, as of the last health check at ${new Date(host.health.last_ok * 1000)
        .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : "";
  return { text: amount, title: `${amount} free where this server's sprints run${at}` };
}

export interface ProgramsCell { text: string; title: string }

/** A server's program access for the table: the ids it runs, with paused and closed
 *  programs left out — they cannot take work, so listing them only lengthens the
 *  cell (they are still ticked in the server's dialog, which edits the real list).
 *  Hovering names them in full. While the program list is still loading, every id
 *  is shown rather than a filtered-down list that would be briefly wrong. */
export function programsCell(host: LedgerHost, programs: ProgramRow[] | undefined): ProgramsCell {
  const byId = new Map((programs ?? []).map((p) => [p.id, p]));
  const name = (id: string) => (byId.get(id)?.title ? `${id} — ${byId.get(id)!.title}` : id);
  if (host.programs === null) {
    const active = (programs ?? []).filter((p) => p.status === "active");
    return {
      text: "all",
      title: active.length ? `every program:\n${active.map((p) => `${p.id} — ${p.title}`).join("\n")}`
                           : "every program",
    };
  }
  if (!host.programs.length) return { text: "none", title: "this server runs no program's work" };
  if (!programs) return { text: host.programs.join(", "), title: host.programs.map(name).join("\n") };
  const active = host.programs.filter((id) => byId.get(id)?.status === "active");
  const hidden = host.programs.filter((id) => !active.includes(id));
  const note = hidden.length
    ? `\nnot shown (paused or closed): ${hidden.map(name).join(", ")}`
    : "";
  if (!active.length) {
    return { text: "—", title: `no program that can take work${note}` };
  }
  return { text: active.join(", "), title: active.map(name).join("\n") + note };
}

// Two short lines: the pips and the memory line a server may or may not have.
const ROW_CONTENT_HEIGHT = 32;

const PIP = { width: 7, height: 11, borderRadius: 1, display: "inline-block" } as const;
const PIP_FILL: Record<string, string> = {
  full: "var(--machine)",
  part: "linear-gradient(to top, var(--machine) 50%, var(--hairline) 50%)",
  empty: "var(--hairline)",
};

/** A hover explanation, the way the rest of the dashboard does them (a real
 *  tooltip, not the browser's `title`, which needs a long pause and often never
 *  shows at all). Multi-line labels keep their line breaks. */
function Hover({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Tooltip withArrow multiline w={340} openDelay={120}
             transitionProps={{ duration: 0 }}
             label={<span style={{ whiteSpace: "pre-line" }}>{label}</span>}>
      {children}
    </Tooltip>
  );
}

/** Text that says "there is more on hover", as the call log's headers do. */
const hoverable = { borderBottom: "1px dotted currentColor", cursor: "help" } as const;

function Pips({ states, label }: { states: string[]; label: string }) {
  if (!states.length) return <Text size="xs" c="dimmed">—</Text>;
  return (
    <Hover label={label}>
      <span style={{ display: "inline-flex", gap: 2, alignItems: "center", cursor: "help" }}>
        {states.map((s, i) => (
          <span key={i} style={{ ...PIP, background: PIP_FILL[s] }} />
        ))}
      </span>
    </Hover>
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
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });

  const open = (host: LedgerHost) => setConfiguring(host);

  // One line for the whole pool instead of a paragraph under every server: the
  // paths are reference material for a cleanup, not something to read each visit.
  const leftovers = hosts.flatMap((h) => (h.leftover ?? []).map((l) => ({ host: h.name, ...l })));
  const leftoverCounts = hosts
    .filter((h) => (h.leftover ?? []).length)
    .map((h) => `${h.leftover!.length} on ${h.name}`)
    .join(", ");
  const leftoverPaths = leftovers.map((l) => `${l.path} (${leftoverLabel(l.status)})`).join("\n");

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
            <Table.Th>Disk</Table.Th>
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
            const free = diskCell(h);
            const access = programsCell(h, programs.data);
            return (
              <Table.Tr key={h.name} role="button" tabIndex={0} aria-label={`Configure ${h.name}`}
                        style={{ cursor: "pointer" }}
                        onClick={() => open(h)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(h); }
                        }}>
                <Table.Td className="mono">
                  {hostLabel(h) === h.name ? h.name : (
                    <Hover label={`filed under "${h.name}"`}>
                      <span style={hoverable}>{hostLabel(h)}</span>
                    </Hover>
                  )}
                </Table.Td>
                <Table.Td>
                  {/* Every row is as tall as a row that declares memory, and its
                      content sits in the middle of that height — a server with no
                      memory line must not leave a blank line hanging under it. */}
                  <div style={{ minHeight: ROW_CONTENT_HEIGHT, display: "flex",
                                flexDirection: "column", justifyContent: "center" }}>
                    <Group gap={6} wrap="nowrap">
                      <Pips states={cpu.pips}
                            label={cpu.per > 1
                              ? `${cpu.label} cores in use — one square is ${cpu.per} cores`
                              : `${cpu.label} cores in use`} />
                      <Text size="xs" c="dimmed">{cpu.label}</Text>
                    </Group>
                    {mem && <Text size="xs" c="dimmed">{mem.label} GB memory</Text>}
                  </div>
                </Table.Td>
                <Table.Td>
                  <Pips states={cards.map((c) => c.state)}
                        label={cards.map((c) => c.title).join("\n")} />
                </Table.Td>
                <Table.Td>
                  <Hover label={free.title}>
                    <span style={{ ...hoverable, whiteSpace: "nowrap",
                                   color: free.color, fontWeight: free.color ? 600 : undefined }}>
                      {free.text}
                    </span>
                  </Hover>
                </Table.Td>
                <Table.Td>
                  <Hover label={access.title}><span style={hoverable}>{access.text}</span></Hover>
                </Table.Td>
                <Table.Td>
                  <Hover label={status.detail}>
                    <span style={{ ...hoverable, whiteSpace: "nowrap" }}>
                      <span style={{ color: status.color, marginRight: 6 }}>{status.mark}</span>
                      {status.key}
                    </span>
                  </Hover>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      {leftovers.length > 0 && (
        <Text size="xs" c="dimmed" style={{ marginTop: 8 }}>
          <Hover label={leftoverPaths}>
            <span style={hoverable}>Run directories no sprint is using: {leftoverCounts}</span>
          </Hover>
          {" — hover for the paths, remove by hand."}
        </Text>
      )}
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
