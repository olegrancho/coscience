import { useState } from "react";
import { Button, Group, Stack, Text, Textarea } from "@mantine/core";
import Md from "./Md";
import { RelTime } from "./ui";
import { UserChip, useIsMine, OTHER_SHADE } from "../auth";
import type { FeedbackThreadT } from "../api";

export function FeedbackThread({ thread, onReply, onComplete, onSeen, respondsNow = true }:
  { thread: FeedbackThreadT; onReply: (t: string) => void; onComplete: () => void; onSeen: () => void; respondsNow?: boolean }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const isMine = useIsMine();
  const first = thread.messages[0];
  const toggle = () => { const n = !open; setOpen(n); if (n && thread.agent_unseen) onSeen(); };
  return (
    <div style={{ border: "1px solid var(--hairline)", borderRadius: 8 }}>
      <div onClick={toggle} style={{ cursor: "pointer", padding: "8px 12px",
        opacity: thread.status === "complete" ? 0.6 : 1 }}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" lineClamp={open ? undefined : 1}>{first?.text}</Text>
          {thread.agent_unseen && <span className="pill" style={{ "--st": "var(--signal)" } as any}><span className="dot" />reply</span>}
        </Group>
      </div>
      {open && (
        <div style={{ padding: "0 12px 12px", borderTop: "1px solid var(--hairline)" }}>
          <Stack gap={7} mt={9}>
            {thread.messages.map((m, i) => (
              <div key={i} style={{ background: m.role === "human" && isMine(m.by) ? "var(--paper)" : OTHER_SHADE,
                borderRadius: 8, padding: "7px 11px" }}>
                <div className="md-tight"><Md>{m.text}</Md></div>
                <Group gap={8} mt={2} wrap="nowrap">
                  {m.role === "human" ? <UserChip username={m.by} /> : <Text size="xs" c="dimmed">{m.role === "pm" ? "PM" : "Agent"}</Text>}
                  <Text size="xs" c="dimmed"><RelTime at={m.at} /></Text>
                </Group>
              </div>
            ))}
          </Stack>
          {!respondsNow && <Text size="xs" c="dimmed" mt={7}>The agent will respond when this sprint runs.</Text>}
          {thread.status === "open" && (
            <Group gap={8} mt={9} align="flex-end">
              <Textarea style={{ flex: 1 }} autosize minRows={1} placeholder="Reply…"
                value={draft} onChange={(e) => setDraft(e.currentTarget.value)} />
              <Button size="xs" disabled={!draft.trim()} onClick={() => { onReply(draft.trim()); setDraft(""); }}>Send</Button>
              <Button size="xs" variant="default" onClick={onComplete}>Mark complete</Button>
            </Group>
          )}
        </div>
      )}
    </div>
  );
}
