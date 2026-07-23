import { ActionIcon, Badge, Button, Card, Group, Loader, Menu, SegmentedControl, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import Md from "../components/Md";
import { Transcript } from "../components/Transcript";
import { api, type ChatScope } from "../api";
import { BackLink, RelTime } from "../components/ui";
import { UserChip, useIsMine, OTHER_SHADE } from "../auth";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function ChatView() {
  const { id = "" } = useParams();
  const [sp] = useSearchParams();
  const wantChat = sp.get("c") || "";
  const qc = useQueryClient();
  const isMine = useIsMine();
  const [active, setActive] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [expanded, setExpanded] = useState(false);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const chats = useQuery({
    queryKey: ["chats", id], queryFn: () => api.listChats(id), refetchInterval: 5000,
  });

  // Default to the most recently active thread once the list loads. A `?c=` param
  // (e.g. from "Open chat" on an artifact) takes priority over the list heuristic,
  // since the list can be a stale cache that doesn't yet include a brand-new thread.
  useEffect(() => {
    if (active) return;
    if (wantChat) { setActive(wantChat); return; }
    const list = chats.data;
    if (!list) return;
    if (list.length) setActive([...list].sort((a, b) => b.last_at - a.last_at)[0].id);
  }, [chats.data, active, wantChat]);

  const thread = useQuery({
    queryKey: ["chat", id, active], queryFn: () => api.getChatThread(id, active),
    enabled: !!active,
    refetchInterval: (q) => (q.state.data?.busy ? 1500 : false),
  });
  const refreshChats = () => qc.invalidateQueries({ queryKey: ["chats", id] });

  // Bound-chat right panel: the thread is "bound" when it carries at least one
  // artifact id. These hooks stay unconditional (top level) per Rules of Hooks —
  // `enabled` gates the actual fetching so unbound chats just skip the network call.
  const aid = thread.data?.artifacts?.[0] ?? "";
  const bound = !!(thread.data?.artifacts && thread.data.artifacts.length);
  const busy = !!thread.data?.busy;
  const work = useQuery({
    queryKey: ["work", id, aid],
    queryFn: () => api.listArtifactWorkFiles(id, aid),
    enabled: !!aid,
    refetchInterval: busy ? 2000 : false,
  });
  const files = work.data ?? [];
  const isImageName = (n: string) => /\.(png|jpe?g|gif|svg|webp)$/i.test(n);
  const isBinaryName = (n: string) => /\.(png|jpe?g|gif|svg|webp|pdf|zip|npy|npz|h5|hdf5|pkl|parquet|bin|ico|mp4|mov)$/i.test(n);
  // A figure's deliverable IS the image — prefer it over any build script so the
  // panel shows the picture, not the .py source. Otherwise render the first text file.
  const imgName = files.find(isImageName) ?? "";
  const textName = files.find((n) => !isBinaryName(n)) ?? "";
  const workName = imgName || textName || files[0] || "";
  const workfile = useQuery({
    queryKey: ["workfile", id, aid, workName],
    queryFn: () => api.readArtifactWorkFile(id, aid, workName!),
    enabled: !!workName && !imgName,       // images render via <img>, not the JSON reader
    refetchInterval: busy ? 2000 : false,
  });

  // One-shot refresh when a turn stops running, so the final edits show up even
  // though polling itself has just switched off.
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      qc.invalidateQueries({ queryKey: ["work", id, aid] });
      qc.invalidateQueries({ queryKey: ["workfile", id, aid, workName] });
    }
    wasBusy.current = busy;
  }, [busy, qc, id, aid, workName]);

  const saveVersion = useMutation({
    mutationFn: () => api.saveChatVersion(id, active),
    onSuccess: (result) => {
      const saved = aid ? result[aid] : undefined;
      if (saved) {
        notifications.show({ color: "green", title: "Saved", message: `Version ${saved}` });
      } else {
        notifications.show({ color: "yellow", title: "Nothing to save", message: "No changes since the last version, or the editing session ended." });
      }
      qc.invalidateQueries({ queryKey: ["work", id, aid] });
      qc.invalidateQueries({ queryKey: ["workfile", id, aid, workName] });
    },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't save version", message: String(e) }),
  });

  const release = useMutation({
    mutationFn: () => api.releaseChat(id, active),
    onSuccess: ({ thread: t2, saved }) => {
      qc.setQueryData(["chat", id, active], t2);
      const vid = aid ? saved[aid] : undefined;
      notifications.show({ color: "green", title: "Released",
        message: vid ? `Saved version ${vid} — artifact freed.`
                     : "Artifact freed (no new changes to save)." });
      qc.invalidateQueries({ queryKey: ["work", id, aid] });
      refreshChats();
    },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't release", message: String(e) }),
  });

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
  const submit = () => { const m = draft.trim(); if (m && !busy) send.mutate(m); };
  const doRename = () => {
    const name = window.prompt("Rename chat", t?.title ?? "");
    if (name && name.trim()) rename.mutate(name.trim());
  };
  const doDelete = (tid: string, title: string) => {
    if (window.confirm(`Delete chat “${title}”? This can't be undone.`)) del.mutate(tid);
  };
  const doRelease = () => {
    if (window.confirm("Done editing? This saves a final version of any changes and releases the artifact. The chat stays as history; the artifact becomes free to edit again.")) release.mutate();
  };

  // Show the 3 most-recent chats by default; unfold for the rest. Always keep the
  // active one visible even if it's older, so its highlight doesn't vanish.
  const sorted = [...(chats.data ?? [])].sort((a, b) => b.last_at - a.last_at);
  let visible = sorted;
  if (!expanded && sorted.length > 3) {
    const top = sorted.slice(0, 3);
    visible = active && !top.some((c) => c.id === active)
      ? [...top.slice(0, 2), sorted.find((c) => c.id === active)!]
      : top;
  }
  const hiddenCount = sorted.length - visible.length;

  // Bound chats break out of the app's centered 980px column to fill the canvas
  // (navbar 232 + canvas padding 60 = 292px reserved), capped so lines don't get
  // absurdly wide. The artifact is the point here, so it gets the room; the chat
  // shrinks to a secondary side column. Unbound chats keep the tight column.
  const breakout = bound
    ? { width: "min(calc(100vw - 292px), 1500px)",
        marginLeft: "calc((980px - min(100vw - 292px, 1500px)) / 2)" }
    : undefined;

  return (
    <Stack gap="lg" style={breakout}>
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

      {/* chat switcher — top row, 3 shown by default, unfold for the rest */}
      <Card padding="xs" radius="md" style={cardStyle}>
        <Group gap={6} style={{ flexWrap: "wrap" }}>
          {visible.map((c) => (
            <button key={c.id} type="button" onClick={() => setActive(c.id)}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer",
                padding: "5px 11px", borderRadius: 999, fontSize: 13,
                border: "1px solid " + (c.id === active ? "var(--machine)" : "var(--hairline)"),
                background: c.id === active ? "var(--machine-weak)" : "transparent",
                color: c.id === active ? "var(--ink)" : "var(--ink-muted)" }}>
              <span style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</span>
              {c.busy && <Loader size={11} color="machine" />}
              {c.scope === "full" && <span title="Full access" style={{ fontSize: 10 }}>🔧</span>}
            </button>
          ))}
          {!expanded && hiddenCount > 0 && (
            <button type="button" className="linklike" onClick={() => setExpanded(true)}
                    style={{ fontSize: 13, padding: "5px 8px" }}>+{hiddenCount} more</button>
          )}
          {expanded && sorted.length > 3 && (
            <button type="button" className="linklike" onClick={() => setExpanded(false)}
                    style={{ fontSize: 13, padding: "5px 8px" }}>Show fewer</button>
          )}
          <Button size="xs" variant="subtle" color="machine" loading={create.isPending}
                  onClick={() => create.mutate()}>+ New</Button>
        </Group>
      </Card>

      {/* active conversation — the standard body column, same edges as every page.
          Bound chats (t.artifacts.length) split into this column + a live artifact
          panel on the right; unbound chats keep the single-column layout untouched. */}
      {!active ? (
        <Stack gap="md" style={{ minWidth: 0 }}>
          <Card padding="lg" radius="md" style={cardStyle}>
            <Text size="sm" c="dimmed">Start a new chat on the left to talk to the planner.</Text>
          </Card>
        </Stack>
      ) : (
        <div style={bound ? { display: "grid", gridTemplateColumns: "minmax(340px, 400px) minmax(0, 1fr)", gap: 20, alignItems: "start" } : undefined}>
          <Stack gap="md" style={{ minWidth: 0 }}>
              <Card padding="sm" radius="md" style={cardStyle}>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
                    <Text fw={600} size="sm" truncate>{t?.title ?? "…"}</Text>
                    {t?.scope === "full" && (
                      <Tooltip label="This chat can run commands and edit files in the workdir." withArrow>
                        <Badge size="sm" color="orange" variant="light">full access</Badge>
                      </Tooltip>
                    )}
                  </Group>
                  <Group gap={8} wrap="nowrap">
                    <SegmentedControl
                      size="xs" value={t?.scope ?? "read"} disabled={setScope.isPending || busy}
                      onChange={(v) => setScope.mutate(v as ChatScope)}
                      data={[{ label: "Read-only", value: "read" }, { label: "Full", value: "full" }]}
                    />
                    <Menu position="bottom-end" withArrow>
                      <Menu.Target>
                        <ActionIcon variant="subtle" color="gray" aria-label="chat actions">⋯</ActionIcon>
                      </Menu.Target>
                      <Menu.Dropdown>
                        <Menu.Item onClick={doRename}>Rename…</Menu.Item>
                        <Menu.Item color="red" onClick={() => doDelete(active, t?.title ?? "")}>Delete…</Menu.Item>
                      </Menu.Dropdown>
                    </Menu>
                  </Group>
                </Group>
              </Card>

              <Card padding="lg" radius="md" style={bound ? { ...cardStyle, maxHeight: "calc(100vh - 340px)", overflow: "auto" } : cardStyle}>
                {thread.isLoading ? <Loader color="machine" /> : (t?.messages.length ?? 0) === 0 && !busy ? (
                  <Text size="sm" c="dimmed">No messages yet — ask the planner something below.</Text>
                ) : (
                  <Stack gap={12}>
                    {(t?.messages ?? []).map((m, i) => (
                      <div key={i} style={{
                        alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                        maxWidth: m.role === "user" ? "72%" : "90%",
                        background: m.role !== "user" ? "var(--paper)"
                          : isMine(m.by) ? "var(--machine-weak)" : OTHER_SHADE,
                        border: "1px solid var(--hairline)", borderRadius: 10, padding: "9px 13px",
                      }}>
                        <Group gap={5} mb={3} wrap="nowrap">
                          {m.role === "user" ? <UserChip username={m.by} /> : <Text size="xs" c="dimmed">PM</Text>}
                          <Text size="xs" c="dimmed">· <RelTime at={m.at} /></Text>
                        </Group>
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
          </Stack>

          {bound && (
            <Card padding="lg" radius="md" style={{ ...cardStyle, position: "sticky", top: 16 }}>
              <Stack gap={12}>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                    <Text fw={600} size="md" className="mono" truncate>{aid}</Text>
                    <Tooltip label="This chat can read and write this artifact's working files while it's active." withArrow>
                      <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>🔒 bound</Text>
                    </Tooltip>
                  </Group>
                  <Group gap={10} wrap="nowrap">
                    <Link to={`/programs/${id}/artifacts/${aid}`} className="view" style={{ whiteSpace: "nowrap" }}>full view →</Link>
                    <Button
                      size="xs" color="green" variant="light" loading={saveVersion.isPending} disabled={busy}
                      onClick={() => saveVersion.mutate()}
                    >
                      Save as version
                    </Button>
                    <Tooltip label="Save a final version and release the lock — frees the artifact; the chat stays as history." withArrow>
                      <Button size="xs" variant="default" loading={release.isPending} disabled={busy}
                              onClick={doRelease}>
                        Release
                      </Button>
                    </Tooltip>
                  </Group>
                </Group>

                {work.isLoading ? (
                  <Loader size="sm" color="machine" />
                ) : imgName ? (
                  <div style={{ maxHeight: "calc(100vh - 190px)", overflow: "auto", textAlign: "center" }}>
                    <img src={`${api.artifactWorkRawUrl(id, aid, imgName)}?t=${work.dataUpdatedAt}`}
                         alt={imgName} style={{ maxWidth: "100%" }} />
                  </div>
                ) : !workName || workfile.data?.binary ? (
                  <Stack gap={6}>
                    <Text size="sm" c="dimmed">Nothing to preview yet — ask the planner to create the file, or save a version.</Text>
                    <Link to={`/programs/${id}/artifacts/${aid}`} className="view">open artifact</Link>
                  </Stack>
                ) : workfile.isLoading ? (
                  <Loader size="sm" color="machine" />
                ) : (
                  <div className="report-leaf" style={{ maxHeight: "calc(100vh - 190px)", overflow: "auto" }}>
                    <Md>{workfile.data?.content ?? ""}</Md>
                  </div>
                )}
              </Stack>
            </Card>
          )}
        </div>
      )}
    </Stack>
  );
}
