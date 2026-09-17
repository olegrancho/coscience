import { useState } from "react";
import { Button, Card, Group, Stack, Text, Textarea } from "@mantine/core";
import { api, type Sprint } from "../api";
import { AbsTime } from "./ui";

const cardStyle = { border: "1px solid var(--signal-line)", background: "var(--signal-weak)" };

/** The record + answer form for a sprint on hold, asking for help. A worker
 *  agent or the platform escalated it (§8.1/§8.2); `level: "pm"` means the PM
 *  will pick it up on its next cycle (no buttons — there's nothing for a human
 *  to do yet), `level: "human"` means it needs a person now. */
export default function EscalationPanel({ sprint, onDone }: { sprint: Sprint; onDone: () => void }) {
  const esc = sprint.escalation;
  const [instructions, setInstructions] = useState("");
  const [moving, setMoving] = useState(false);
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!esc) return null;

  const answer = async (body: { action: "resume" | "reallocate" | "stop"; instructions?: string; host?: string }) => {
    setBusy(true); setError("");
    try {
      await api.answerEscalation(sprint.id, { ...body, thread_id: esc.thread_id });
      onDone();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const resume = () => answer({ action: "resume", instructions });
  const confirmMove = () => answer({ action: "reallocate", host, instructions });
  const stop = () => {
    if (!window.confirm("Stop this sprint? It will be marked failed; a human can resume it later.")) return;
    void answer({ action: "stop" });
  };

  const hostsAllowed = esc.hosts_allowed ?? [];
  const noHosts = hostsAllowed.length === 0;

  return (
    <Card id="sec-escalation" padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <div className="eyebrow" style={{ color: "var(--signal)" }}>
          {esc.level === "human" ? "Needs you" : "escalated to the planner"}
        </div>
        <Text size="xs" c="dimmed">
          raised by {esc.by} on {esc.host || "—"} · <AbsTime at={esc.at} />
        </Text>
      </Group>

      <Stack gap={6} mt={10}>
        <Text size="sm"><b>What happened</b> — <span>{esc.what}</span></Text>
        <Text size="sm"><b>Tried</b> — <span>{esc.tried}</span></Text>
        {esc.may_have_broken_something && (
          <Text size="sm" fw={600} c="red">This may have damaged something — check before resuming.</Text>
        )}
        <Text size="sm"><b>Needs</b> — <span>{esc.needs}</span></Text>
      </Stack>

      {esc.stop_requested ? (
        <Text size="sm" mt={14} fw={600} c="red">
          Stopping — the platform will stop this sprint on its next cycle.
        </Text>
      ) : (
        <>
          {esc.level === "pm" && (
            <Text size="sm" mt={14} c="dimmed">The PM will answer this on its next cycle</Text>
          )}

          {/* The plan's global constraint: a human may answer at either level —
           *  a pm-level escalation isn't exclusive to the PM, it's just where the
           *  answer normally comes from first. */}
          {esc.level === "pm" && (
            <Text size="xs" mt={12} c="dimmed">Or answer it yourself now:</Text>
          )}
          <Stack gap={10} mt={esc.level === "pm" ? 8 : 14}>
            <Textarea label="Instructions for the agent"
              description="The agent reads only these instructions; replies in the thread below are for people."
              placeholder="Direction to include when the agent picks this back up…"
              autosize minRows={2} value={instructions}
              onChange={(e) => setInstructions(e.currentTarget.value)} />
            <Group gap={8}>
              <Button color="signal" onClick={resume} loading={busy} disabled={busy}>Resume</Button>
              <Button variant="default" disabled={busy || noHosts}
                title={noHosts ? "No other server this program may use" : undefined}
                onClick={() => setMoving((m) => !m)}>
                Move to another server
              </Button>
              <Button variant="default" color="red" onClick={stop} disabled={busy}>Stop sprint</Button>
            </Group>
            {moving && !noHosts && (
              <Group gap={8} align="flex-end">
                <label style={{ display: "inline-flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
                  <span style={{ color: "var(--ink-muted)" }}>Destination server</span>
                  <select aria-label="Destination server" value={host}
                    onChange={(e) => setHost(e.target.value)}
                    style={{ fontSize: 13, padding: "5px 8px", background: "var(--surface)",
                             color: "var(--ink)", border: "1px solid var(--hairline)", borderRadius: 6 }}>
                    <option value="">choose a server…</option>
                    {hostsAllowed.map((h) => <option key={h} value={h}>{h}</option>)}
                  </select>
                </label>
                <Button size="sm" onClick={confirmMove} loading={busy} disabled={busy || !host}>Confirm move</Button>
              </Group>
            )}
            {error && <Text size="sm" c="red">{error}</Text>}
          </Stack>
        </>
      )}
    </Card>
  );
}
