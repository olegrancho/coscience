import { Table, Text, Tooltip } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, type CallRow } from "../api";
import { dayTime } from "./timefmt";

/** Colour by outcome. `running` is neutral rather than green: it has not
 *  succeeded yet, and on 09-04 most calls that were running went on to die. */
const STATUS_COLOR: Record<string, string> = {
  ok: "var(--st-done, #2f9e44)",
  running: "var(--machine)",
  "rate-limited": "var(--signal)",
  failed: "var(--signal)",
  escaped: "var(--signal)",
  lost: "var(--ink-faint)",
};

function time(ts: number | null): string {
  if (!ts) return "—";
  return dayTime(ts);
}

function dur(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
}

function pct(w: { pct: number } | null): string {
  return w ? `${Math.round(w.pct)}%` : "—";
}

function money(cost: number | null): string {
  return cost === null || cost === undefined ? "—" : `$${cost.toFixed(2)}`;
}

const PAGE_SIZE = 50;

/** When a call happened, for date filtering: its end, or its start while it is
 *  still running. A running call has no end and must not vanish from a range
 *  that covers today. */
function occurredAt(c: CallRow): number {
  return c.ended_at ?? c.started_at ?? 0;
}

/** Local midnight of a `yyyy-mm-dd` input value, in seconds. Parsed by hand
 *  rather than via `new Date(value)`, which reads a bare date as UTC and would
 *  shift the boundary by the viewer's offset. */
function dayStart(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime() / 1000;
}

const DAY = 86400;

/** Every Claude call the platform has made, newest first: what it was for, how
 *  it ended, and what it cost. Before this existed the wiki's spend was invisible
 *  on this page entirely and a killed run left no trace at all. */
export default function CallLog() {
  const [kind, setKind] = useState("");
  const [program, setProgram] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const log = useQuery({
    queryKey: ["call-log"],
    queryFn: () => api.getCallLog(200),
    refetchInterval: 15_000,
  });

  const calls: CallRow[] = useMemo(() => log.data?.calls ?? [], [log.data]);
  const kinds = useMemo(
    () => Array.from(new Set(calls.map((c) => c.kind).filter(Boolean))).sort(),
    [calls]);
  const programs = useMemo(
    () => Array.from(new Set(calls.map((c) => c.program).filter(Boolean))).sort(),
    [calls]);

  const fromTs = from ? dayStart(from) : null;
  // Inclusive of the whole to-day: a range of 3rd..3rd must keep 23:30 on the 3rd.
  const toStart = to ? dayStart(to) : null;
  const toTs = toStart === null ? null : toStart + DAY;

  const rows = useMemo(() => calls.filter((c) => {
    if (kind && c.kind !== kind) return false;
    if (program && c.program !== program) return false;
    const when = occurredAt(c);
    if (fromTs !== null && when < fromTs) return false;
    if (toTs !== null && when >= toTs) return false;
    return true;
  }), [calls, kind, program, fromTs, toTs]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  // Narrowing the filters can strand you past the end of the new result set.
  useEffect(() => { setPage(0); }, [kind, program, from, to]);
  const current = Math.min(page, pageCount - 1);
  const shown = rows.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE);

  if (log.isLoading) return <Text size="sm" c="dimmed">Loading calls…</Text>;
  if (!calls.length) {
    return <Text size="sm" c="dimmed">No Claude calls recorded yet for this substrate.</Text>;
  }

  const spend = rows.reduce((sum, c) => sum + (c.cost ?? 0), 0);

  return (
    <>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <select aria-label="Filter by kind" value={kind}
                onChange={(e) => setKind(e.target.value)}
                style={{ fontSize: 12, padding: "3px 6px" }}>
          <option value="">all kinds</option>
          {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <select aria-label="Filter by program" value={program}
                onChange={(e) => setProgram(e.target.value)}
                style={{ fontSize: 12, padding: "3px 6px" }}>
          <option value="">all programs</option>
          {programs.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <label style={{ fontSize: 12, color: "var(--ink-muted)" }}>
          from{" "}
          <input type="date" aria-label="From date" value={from}
                 onChange={(e) => setFrom(e.target.value)}
                 style={{ fontSize: 12, padding: "2px 4px" }} />
        </label>
        <label style={{ fontSize: 12, color: "var(--ink-muted)" }}>
          to{" "}
          <input type="date" aria-label="To date" value={to}
                 onChange={(e) => setTo(e.target.value)}
                 style={{ fontSize: 12, padding: "2px 4px" }} />
        </label>
        {(kind || program || from || to) && (
          <button onClick={() => { setKind(""); setProgram(""); setFrom(""); setTo(""); }}
                  style={{ fontSize: 12, padding: "3px 8px" }}>clear</button>
        )}
        <Text size="xs" c="dimmed">
          {rows.length} {rows.length === 1 ? "call" : "calls"} · ${spend.toFixed(2)}
        </Text>
      </div>

      <div style={{ overflowX: "auto" }}>
        <Table striped highlightOnHover style={{ fontSize: 12 }}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>type</Table.Th>
              <Table.Th>model</Table.Th>
              <Table.Th>program</Table.Th>
              <Table.Th>sprint</Table.Th>
              <Table.Th>started</Table.Th>
              <Table.Th>ended</Table.Th>
              <Table.Th>took</Table.Th>
              <Table.Th>status</Table.Th>
              <Table.Th>
                <Tooltip label="The 5h window either side of this call. Calls overlap, so the change is not what this one call consumed.">
                  <span style={{ borderBottom: "1px dotted currentColor" }}>5h before → after</span>
                </Tooltip>
              </Table.Th>
              <Table.Th style={{ textAlign: "right" }}>cost</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td className="mono">{c.kind || "—"}</Table.Td>
                <Table.Td className="mono" style={{ color: "var(--ink-muted)" }}>
                  {c.model || "—"}
                </Table.Td>
                <Table.Td className="mono">{c.program || "—"}</Table.Td>
                <Table.Td className="mono" style={{ color: "var(--ink-muted)" }}>
                  {c.sprint || "—"}
                </Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{time(c.started_at)}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{time(c.ended_at)}</Table.Td>
                <Table.Td className="mono">{dur(c.duration)}</Table.Td>
                <Table.Td className="mono"
                          style={{ color: STATUS_COLOR[c.status] ?? "var(--ink)" }}>
                  {c.status || "—"}
                </Table.Td>
                <Table.Td className="mono" style={{ whiteSpace: "nowrap" }}>
                  {pct(c.limits_before)} → {pct(c.limits_after)}
                </Table.Td>
                <Table.Td className="mono" style={{ textAlign: "right" }}>{money(c.cost)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>

      {pageCount > 1 && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
          <button onClick={() => setPage(current - 1)} disabled={current === 0}
                  style={{ fontSize: 12, padding: "3px 8px" }}>Previous</button>
          <Text size="xs" c="dimmed">page {current + 1} of {pageCount}</Text>
          <button onClick={() => setPage(current + 1)} disabled={current >= pageCount - 1}
                  style={{ fontSize: 12, padding: "3px 8px" }}>Next</button>
        </div>
      )}
    </>
  );
}
