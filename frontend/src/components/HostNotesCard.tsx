import { Button, Card, Group, Stack, Text, Textarea } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type HostNotes, type HostReport, type LedgerHost } from "../api";
import Md from "./Md";
import { hostLabel } from "./HostsCard";
import { hostAllows } from "./programAccess";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

const STALE = " (no longer used by this program)";

export interface NoteRow {
  host: string;        // the name every lease, probe and note file is keyed on
  label: string;       // what to call it on screen
  note: string;
  reports: HostReport[];
  stale: boolean;      // has a note or reports, but takes none of this program's work
}

/** One row per server a worker of this program can land on, plus any server that
 *  still holds a note or an unread report from when it could — those are the ones
 *  whose notes would otherwise be unreachable, and their reports unclearable. */
export function noteRows(
  hosts: LedgerHost[], programId: string, data: HostNotes,
): NoteRow[] {
  const known = new Map(hosts.map((h) => [h.name, h]));
  const names = new Set<string>();
  for (const h of hosts) if (hostAllows(h, programId)) names.add(h.name);
  for (const host of Object.keys(data.notes)) names.add(host);
  for (const r of data.reports) names.add(r.host);

  // This machine first — it is where work lands by default — then alphabetical.
  const order = (name: string) => (name === "local" ? "" : name);
  return [...names]
    .sort((a, b) => order(a).localeCompare(order(b)))
    .map((name) => {
      const h = known.get(name);
      return {
        host: name,
        label: name === "local" ? "this machine" : h ? hostLabel(h) : name,
        note: data.notes[name] ?? "",
        reports: data.reports.filter((r) => r.host === name),
        stale: !h || !hostAllows(h, programId),
      };
    });
}

function Row({ row, programId }: { row: NoteRow; programId: string }) {
  const qc = useQueryClient();
  // The note being edited, or null when the row is just displaying it.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      // The response is the whole page of notes and what is left of the reports
      // (saving clears this server's), so there is nothing to refetch.
      qc.setQueryData(["host-notes", programId], await api.setHostNote(
        programId, row.host, draft ?? "", row.reports.map((r) => r.id)));
      setDraft(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ borderTop: "1px solid var(--hairline)", paddingTop: 10 }}>
      <Group justify="space-between" align="baseline" wrap="nowrap" mb={6}>
        <Text size="sm" fw={600}>
          <span className="mono">{row.label}</span>
          {row.stale && <Text span size="xs" c="dimmed">{STALE}</Text>}
        </Text>
        {draft === null && (
          <button type="button" className="linklike"
                  aria-label={`Edit notes on ${row.label}`}
                  onClick={() => setDraft(row.note)}>Edit</button>
        )}
      </Group>

      {draft === null ? (
        row.note
          ? <div className="md-tight"><Md>{row.note}</Md></div>
          : <Text size="sm" c="dimmed">No notes yet.</Text>
      ) : (
        <Stack gap={8}>
          <Textarea autosize minRows={3} value={draft} autoFocus
                    aria-label={`Notes on ${row.label}`}
                    placeholder="What this server is for in this program: environments that work, quirks, what it is good or bad for."
                    onChange={(e) => setDraft(e.currentTarget.value)} />
          <Group gap={8}>
            <Button size="xs" variant="light" color="machine" disabled={saving} onClick={save}>Save</Button>
            <Button size="xs" variant="subtle" color="gray"
                    onClick={() => { setDraft(null); setError(""); }}>Cancel</Button>
          </Group>
        </Stack>
      )}

      {row.reports.map((r) => (
        <Text key={r.id} size="xs" c="dimmed" mt={4}>
          {`from ${r.sprint_id} (${r.source}): ${r.text}`}
        </Text>
      ))}
      {error && <Text size="xs" c="red" mt={4}>{error}</Text>}
    </div>
  );
}

/** A program's own notes per server, one row each: what the workers it places
 *  there should know, and what finished or escalated sprints reported and the PM
 *  has not folded in yet. */
export default function HostNotesCard({ programId }: { programId: string }) {
  const notes = useQuery({ queryKey: ["host-notes", programId], queryFn: () => api.getHostNotes(programId) });
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });

  // Nothing is drawn until both have answered. A row's Edit seeds its draft from
  // the notes query and Save writes that draft, so a row drawn early would offer
  // an empty box that overwrites a real note — and with no ledger yet, every
  // server would be wrongly marked as no longer used by this program.
  if (!notes.data || !ledger.data) return null;
  const rows = noteRows(ledger.data.hosts ?? [], programId, notes.data);
  if (!rows.length) return null;

  return (
    <Card id="sec-host-notes" padding="lg" radius="md" style={cardStyle}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>
        server notes — what each server is for in this program
      </div>
      <Text size="xs" c="dimmed" mb="sm">
        Every worker placed on a server reads its note; the PM keeps them up to date from
        what finished and escalated sprints report. Saving a note marks that server's
        reports read.
      </Text>
      <Stack gap={10}>
        {rows.map((r) => <Row key={r.host} row={r} programId={programId} />)}
      </Stack>
    </Card>
  );
}
