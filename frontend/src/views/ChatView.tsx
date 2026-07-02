import { ActionIcon, Badge, Button, Card, Group, Loader, Menu, SegmentedControl, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import Md from "../components/Md";
import { Transcript } from "../components/Transcript";
import { api, type ChatScope } from "../api";
import { BackLink, RelTime } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function ChatView() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [active, setActive] = useState<string>("");
  const [draft, setDraft] = useState("");

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const chats = useQuery({
    queryKey: ["chats", id], queryFn: () => api.listChats(id), refetchInterval: 5000,
  });

  // Default to the most recently active thread once the list loads.
  useEffect(() => {
    const list = chats.data;
    if (!list || active) return;
    if (list.length) setActive([...list].sort((a, b) => b.last_at - a.last_at)[0].id);
  }, [chats.data, active]);

  const thread = useQuery({
    queryKey: ["chat", id, active], queryFn: () => api.getChatThread(id, active),
    enabled: !!active,
    refetchInterval: (q) => (q.state.data?.busy ? 1500 : false),
  });
  const refreshChats = () => qc.invalidateQueries({ queryKey: ["chats", id] });

  const create = useMutation({
    mutationFn: () => api.createChat(id),
    onSuccess: (t) => { setActive(t.id); refreshChats(); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't create chat", message: String(e) }),
  });
  const send = useMutation({
    mutationFn: (message: string) => api.sendChatMessage(id, active, message),
    onSuccess: (t) => { qc.setQueryData(["chat", id, active], t); setDraft(""); refreshChats(); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't send", message: String(e) }),
  });
  const setScope = useMutation({
    mutationFn: (scope: ChatScope) => api.patchChat(id, active, { scope }),
    onSuccess: (t) => { qc.setQueryData(["chat", id, active], t); refreshChats(); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't change access", message: String(e) }),
  });
  const rename = useMutation({
    mutationFn: (title: string) => api.patchChat(id, active, { title }),
    onSuccess: (t) => { qc.setQueryData(["chat", id, active], t); refreshChats(); },
  });
  const del = useMutation({
    mutationFn: (tid: string) => api.deleteChat(id, tid),
    onSuccess: (_r, tid) => { if (tid === active) setActive(""); refreshChats(); },
  });

  const t = thread.data;
  const busy = !!t?.busy;
  const submit = () => { const m = draft.trim(); if (m && !busy) send.mutate(m); };
  const doRename = () => {
    const name = window.prompt("Rename chat", t?.title ?? "");
    if (name && name.trim()) rename.mutate(name.trim());
  };
  const doDelete = (tid: string, title: string) => {
    if (window.confirm(`Delete chat “${title}”? This can't be undone.`)) del.mutate(tid);
  };

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>{program.data?.title || id}</BackLink>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>
          Chat with the planner
        </h1>
        <Text size="sm" c="dimmed" mt={6} style={{ maxWidth: 660 }}>
          Each chat is its own resumable session in the program's working directory. Read-only
          lets the PM explore and read files to answer; Full lets it run commands and edit files —
          a hands-on research assistant. Separate from sprints, so it won't propose or approve.
        </Text>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "200px minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
        {/* thread list */}
        <Card padding="sm" radius="md" style={cardStyle}>
          <Button fullWidth size="xs" color="machine" mb={8} loading={create.isPending}
                  onClick={() => create.mutate()}>+ New chat</Button>
          <Stack gap={2}>
            {(chats.data ?? []).map((c) => (
              <div key={c.id}
                onClick={() => setActive(c.id)}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 8px",
                  borderRadius: 7, cursor: "pointer",
                  background: c.id === active ? "var(--machine-weak)" : "transparent" }}>
                <span style={{ flex: 1, minWidth: 0, fontSize: 13, whiteSpace: "nowrap",
                  overflow: "hidden", textOverflow: "ellipsis" }}>{c.title}</span>
                {c.busy && <Loader size={11} color="machine" />}
                {c.scope === "full" && <span title="Full access" style={{ fontSize: 10 }}>🔧</span>}
                <ActionIcon size="xs" variant="subtle" color="gray" aria-label="delete chat"
                  onClick={(e) => { e.stopPropagation(); doDelete(c.id, c.title); }}>✕</ActionIcon>
              </div>
            ))}
            {chats.data?.length === 0 && (
              <Text size="xs" c="dimmed" ta="center" py={8}>No chats yet.</Text>
            )}
          </Stack>
        </Card>

        {/* active thread */}
        <Stack gap="md">
          {!active ? (
            <Card padding="lg" radius="md" style={cardStyle}>
              <Text size="sm" c="dimmed">Pick a chat on the left, or start a new one.</Text>
            </Card>
          ) : (
            <>
              <Card padding="sm" radius="md" style={cardStyle}>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                    <Text fw={600} size="sm" truncate>{t?.title ?? "…"}</Text>
                    <ActionIcon size="xs" variant="subtle" color="gray" aria-label="rename" onClick={doRename}>✎</ActionIcon>
                  </Group>
                  <Group gap={8} wrap="nowrap">
                    {t?.scope === "full" && (
                      <Tooltip label="This chat can run commands and edit files in the workdir." withArrow>
                        <Badge size="sm" color="orange" variant="light">full access</Badge>
                      </Tooltip>
                    )}
                    <SegmentedControl
                      size="xs" value={t?.scope ?? "read"} disabled={setScope.isPending || busy}
                      onChange={(v) => setScope.mutate(v as ChatScope)}
                      data={[{ label: "Read-only", value: "read" }, { label: "Full", value: "full" }]}
                    />
                  </Group>
                </Group>
              </Card>

              <Card padding="lg" radius="md" style={cardStyle}>
                {thread.isLoading ? <Loader color="machine" /> : (t?.messages.length ?? 0) === 0 && !busy ? (
                  <Text size="sm" c="dimmed">No messages yet — ask the planner something below.</Text>
                ) : (
                  <Stack gap={12}>
                    {(t?.messages ?? []).map((m, i) => (
                      <div key={i} style={{
                        alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                        maxWidth: m.role === "user" ? "72%" : "90%",
                        background: m.role === "user" ? "var(--machine-weak)" : "var(--paper)",
                        border: "1px solid var(--hairline)", borderRadius: 10, padding: "9px 13px",
                      }}>
                        <Text size="xs" c="dimmed" mb={3}>{m.role === "user" ? "You" : "PM"} · <RelTime at={m.at} /></Text>
                        {m.role === "pm"
                          ? <div className="md-tight"><Md>{m.text}</Md></div>
                          : <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{m.text}</Text>}
                      </div>
                    ))}
                    {busy && (
                      <div style={{ alignSelf: "flex-start", maxWidth: "92%", width: "100%",
                        background: "var(--paper)", border: "1px dashed var(--hairline)", borderRadius: 10, padding: "10px 13px" }}>
                        <Group gap={7} mb={6}><Loader size="xs" color="machine" /><Text size="xs" c="dimmed">PM is working…</Text></Group>
                        <Transcript raw={t?.live ?? ""} />
                      </div>
                    )}
                  </Stack>
                )}
              </Card>

              <Card padding="md" radius="md" style={cardStyle}>
                <Stack gap={8}>
                  <Textarea
                    placeholder={busy ? "PM is working — wait for it to finish…" : "Ask the planner… (⌘↵ to send)"}
                    autosize minRows={2} value={draft} disabled={busy}
                    onChange={(e) => setDraft(e.currentTarget.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
                  />
                  <Button color="machine" loading={send.isPending} disabled={busy}
                          onClick={submit} style={{ alignSelf: "flex-end" }}>
                    Send{send.isPending ? "" : "  (⌘↵)"}
                  </Button>
                </Stack>
              </Card>
            </>
          )}
        </Stack>
      </div>
    </Stack>
  );
}
