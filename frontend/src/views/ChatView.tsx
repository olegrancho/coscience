import { Button, Card, Loader, Stack, Text, Textarea } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import Md from "../components/Md";
import { api } from "../api";
import { BackLink, RelTime } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function ChatView() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const chat = useQuery({ queryKey: ["chat", id], queryFn: () => api.getChat(id) });

  const send = useMutation({
    mutationFn: (message: string) => api.sendChat(id, message),
    onSuccess: (r) => { qc.setQueryData(["chat", id], r.messages); setDraft(""); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't send", message: String(e) }),
  });

  const submit = () => { const m = draft.trim(); if (m) send.mutate(m); };
  const messages = chat.data ?? [];

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>{program.data?.title || id}</BackLink>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>
          Chat with the planner
        </h1>
        <Text size="sm" c="dimmed" mt={6} style={{ maxWidth: 640 }}>
          Ask the PM clarifying questions about this program — it answers with full context,
          but doesn't act. To change anything, use the program's controls (approve, propose,
          guidance, comments).
        </Text>
      </div>

      <Card padding="lg" radius="md" style={cardStyle}>
        {chat.isLoading ? <Loader color="machine" /> : messages.length === 0 ? (
          <Text size="sm" c="dimmed">No messages yet — ask the planner something below.</Text>
        ) : (
          <Stack gap={12}>
            {messages.map((m, i) => (
              <div key={i} style={{
                alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                maxWidth: "82%",
                background: m.role === "user" ? "var(--machine-weak)" : "var(--paper)",
                border: "1px solid var(--hairline)", borderRadius: 10, padding: "9px 13px",
              }}>
                <Text size="xs" c="dimmed" mb={3}>{m.role === "user" ? "You" : "PM"} · <RelTime at={m.at} /></Text>
                {m.role === "pm"
                  ? <div className="md-tight"><Md>{m.text}</Md></div>
                  : <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{m.text}</Text>}
              </div>
            ))}
          </Stack>
        )}
      </Card>

      <Card padding="md" radius="md" style={cardStyle}>
        <Stack gap={8}>
          <Textarea
            placeholder="Ask the planner… (e.g. why did you propose the GPU benchmark? what's the biggest open risk?)"
            autosize minRows={2} value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
          />
          <Button color="machine" loading={send.isPending} onClick={submit}
                  style={{ alignSelf: "flex-end" }}>
            Send{send.isPending ? "" : "  (⌘↵)"}
          </Button>
        </Stack>
      </Card>
    </Stack>
  );
}
