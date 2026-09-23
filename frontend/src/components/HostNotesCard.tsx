import { Badge, Button, Card, Group, Stack, Text, Textarea } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, NoteChangedError, type HostNotes, type HostReport, type NoteHost } from "../api";
import Md from "./Md";

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
 *  whose notes would otherwise be unreachable, and their reports unclearable.
 *
 *  The servers come with the notes (`data.hosts`, O23): the card used to read them
 *  off the whole ledger, polled every ten seconds by every open program page. */
export function noteRows(data: HostNotes): NoteRow[] {
  const hosts: NoteHost[] = data.hosts ?? [];
  const known = new Map(hosts.map((h) => [h.name, h]));
  const names = new Set<string>();
  for (const h of hosts) if (h.allowed) names.add(h.name);
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
        // A display name is what a human chose to call the machine, so it wins even
        // for this one; "this machine" is only the fallback when nobody named it.
        label: h?.label?.trim() || (name === "local" ? "this machine" : name),
        note: data.notes[name] ?? "",
        reports: data.reports.filter((r) => r.host === name),
        stale: !h || !h.allowed,
      };
    });
}

/** What a collapsed row says about its note: its first line, cut short. */
export function notePreview(note: string, max = 90): string {
  const first = note.split("\n").map((l) => l.replace(/^[#>*\-\s]+/, "").trim()).find(Boolean) ?? "";
  return first.length > max ? `${first.slice(0, max - 1)}…` : first;
}

function Row({ row, programId }: { row: NoteRow; programId: string }) {
  const qc = useQueryClient();
  // Collapsed until asked (P12): with several servers the full notes and their
  // reports took more of the page than anything around them.
  const [open, setOpen] = useState(false);
  // The note being edited, or null when the row is just displaying it.
  const [draft, setDraft] = useState<string | null>(null);
  // The note as it stood when editing began. Saving sends it, so a note someone else
  // changed meanwhile is refused rather than silently overwritten (O22).
  const [base, setBase] = useState("");
  // Their version, once a save has been refused because of it.
  const [theirs, setTheirs] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const write = async (text: string, against: string) => {
    setSaving(true);
    setError("");
    try {
      // The response is the whole page of notes and what is left of the reports
      // (saving clears this server's), so there is nothing to refetch.
      qc.setQueryData(["host-notes", programId], await api.setHostNote(
        programId, row.host, text, row.reports.map((r) => r.id), against));
      setDraft(null);
      setTheirs(null);
    } catch (e) {
      if (e instanceof NoteChangedError) {
        setTheirs(e.current);
        qc.invalidateQueries({ queryKey: ["host-notes", programId] });
      } else {
        setError(String(e));
      }
    } finally {
      setSaving(false);
    }
  };

  const startEdit = () => { setOpen(true); setBase(row.note); setTheirs(null); setDraft(row.note); };
  const count = row.reports.length;

  return (
    <div style={{ borderTop: "1px solid var(--hairline)", paddingTop: 8 }}>
      <Group justify="space-between" align="center" wrap="nowrap" gap={10}>
        <button type="button" className="linklike" aria-expanded={open}
                aria-label={`${open ? "Collapse" : "Expand"} notes on ${row.label}`}
                onClick={() => setOpen((v) => !v)}
                style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0, flex: 1,
                         textAlign: "left", color: "var(--ink)", textDecoration: "none" }}>
          <span aria-hidden style={{ fontSize: 11, color: "var(--ink-faint)", width: 10 }}>{open ? "▾" : "▸"}</span>
          <Text span size="sm" fw={600} className="mono" style={{ whiteSpace: "nowrap" }}>{row.label}</Text>
          {row.stale && <Text span size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{STALE}</Text>}
          {!open && (
            <Text span size="xs" c="dimmed" truncate style={{ minWidth: 0 }}>
              {row.note ? notePreview(row.note) : "no notes yet"}
            </Text>
          )}
        </button>
        <Group gap={8} wrap="nowrap">
          {count > 0 && (
            <Badge size="xs" variant="light" color="machine">
              {count} {count === 1 ? "report" : "reports"} unread
            </Badge>
          )}
          {draft === null && (
            <button type="button" className="linklike"
                    aria-label={`Edit notes on ${row.label}`} onClick={startEdit}>Edit</button>
          )}
        </Group>
      </Group>

      {open && (
        <div style={{ paddingLeft: 18, marginTop: 6 }}>
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
              {theirs !== null && (
                <div style={{ border: "1px solid var(--hairline)", borderRadius: 6, padding: "8px 10px" }}>
                  <Text size="xs" c="red" mb={4}>
                    Someone changed this note since you opened it. Your text is kept above;
                    theirs is below. Save again to replace theirs with yours.
                  </Text>
                  {theirs
                    ? <div className="md-tight"><Md>{theirs}</Md></div>
                    : <Text size="xs" c="dimmed">They cleared it.</Text>}
                </div>
              )}
              <Group gap={8}>
                <Button size="xs" variant="light" color="machine" disabled={saving}
                        onClick={() => {
                          // After a refusal, saving again is a deliberate choice to
                          // replace what they wrote, so it goes against their version.
                          const against = theirs ?? base;
                          if (theirs !== null) setBase(theirs);
                          write(draft ?? "", against);
                        }}>
                  {theirs !== null ? "Save mine over theirs" : "Save"}
                </Button>
                <Button size="xs" variant="subtle" color="gray"
                        onClick={() => { setDraft(null); setTheirs(null); setError(""); }}>Cancel</Button>
              </Group>
            </Stack>
          )}

          {row.reports.map((r) => (
            <Text key={r.id} size="xs" c="dimmed" mt={4}>
              {`from ${r.sprint_id} (${r.source}): ${r.text}`}
            </Text>
          ))}
          {row.stale && count > 0 && draft === null && (
            // O22. The planner is no longer shown these — it could not write this note
            // back — so a human is the only one who can mark them read.
            <Group gap={8} mt={6} wrap="nowrap" align="baseline">
              <Text size="xs" c="dimmed">
                This program no longer runs here, so the planner will not fold these in.
              </Text>
              <button type="button" className="linklike" disabled={saving}
                      onClick={() => write(row.note, row.note)}>Mark read</button>
            </Group>
          )}
        </div>
      )}
      {error && <Text size="xs" c="red" mt={4}>{error}</Text>}
    </div>
  );
}

/** A program's own notes per server, one row each: what the workers it places
 *  there should know, and what finished or escalated sprints reported and the PM
 *  has not folded in yet. */
export default function HostNotesCard({ programId }: { programId: string }) {
  const notes = useQuery({ queryKey: ["host-notes", programId], queryFn: () => api.getHostNotes(programId) });

  // Nothing is drawn until the notes have answered. A row's Edit seeds its draft from
  // them and Save writes that draft, so a row drawn early would offer an empty box
  // that overwrites a real note.
  if (!notes.data) return null;
  const rows = noteRows(notes.data);
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
      <Stack gap={8}>
        {rows.map((r) => <Row key={r.host} row={r} programId={programId} />)}
      </Stack>
    </Card>
  );
}
