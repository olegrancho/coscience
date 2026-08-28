import { ActionIcon, Badge, Button, Card, Group, Loader, Popover, Stack, Text, Textarea, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import Md from "../components/Md";
import { FeedbackThread } from "../components/FeedbackThread";
import { api } from "../api";
import { buildArtifactTree, type TreeRow } from "../components/artifactTree";
import { BackLink, DESCRIPTION_FILE, EmptyState, RelTime, StatusBadge, ZoomableImg, canvasBreakout, isImageName, liveChatId } from "../components/ui";
import { UserChip } from "../auth";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/** Renders a version's content, dispatching on `kind`. Reused for both the
 *  current version and the "viewing previous version" preview. */
function VersionContent(
  { pid, aid, kind, vid, files }:
  { pid: string; aid: string; kind: string; vid: string; files: string[] },
) {
  const imgName = files.find(isImageName) ?? "";
  const textLike = kind !== "figure" && kind !== "page";
  const name = (textLike ? files.find((f) => !isImageName(f)) : undefined) ?? files[0];
  const file = useQuery({
    queryKey: ["artifact-file", pid, aid, vid, name],
    queryFn: () => api.readArtifactFile(pid, aid, vid, name!),
    enabled: textLike && !!vid && !!name,
  });

  const hasDesc = kind === "figure" && files.includes(DESCRIPTION_FILE) && !!imgName;
  const desc = useQuery({
    queryKey: ["artifact-desc", pid, aid, vid, DESCRIPTION_FILE],
    queryFn: () => api.readArtifactFile(pid, aid, vid, DESCRIPTION_FILE),
    enabled: hasDesc && !!vid,
  });

  if (kind === "figure") {
    if (!vid) return <Text size="sm" c="dimmed">No content yet.</Text>;
    if (!imgName) return <Text size="sm" c="dimmed">No image in this version — download to view.</Text>;
    return (
      <Stack gap="md">
        <ZoomableImg src={api.artifactVersionRawUrl(pid, aid, vid, imgName)}
                     style={{ maxWidth: "100%" }} alt={imgName} />
        {hasDesc ? (
          desc.isLoading ? <Loader size="sm" color="machine" />
          : desc.error || !desc.data ? <Text size="sm" c="red">Couldn't load the description.</Text>
          : (
            <div className="report-leaf">
              <Md resolveSrc={(src) => api.artifactVersionRawUrl(pid, aid, vid, src)}>
                {desc.data.content}
              </Md>
            </div>
          )
        ) : (
          <Text size="sm" c="dimmed">
            No description yet — a figure should ship a{" "}
            <span className="mono">{DESCRIPTION_FILE}</span> saying what it shows.
          </Text>
        )}
      </Stack>
    );
  }

  if (kind === "page") {
    if (!vid) return <Text size="sm" c="dimmed">No content yet.</Text>;
    return (
      <iframe
        title="artifact page"
        sandbox="allow-scripts"
        src={api.artifactPageUrl(pid, aid, vid, "index.html")}
        style={{ width: "100%", height: 520, border: "1px solid var(--hairline)", borderRadius: 8 }}
      />
    );
  }

  if (!vid || !name) return <Text size="sm" c="dimmed">No content yet.</Text>;
  if (file.isLoading) return <Loader size="sm" color="machine" />;
  if (file.error || !file.data) return <Text size="sm" c="red">Couldn't load the file.</Text>;

  if (kind === "data") {
    if (file.data.binary) return <Text size="sm" c="dimmed">Binary — download to view.</Text>;
    return (
      <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
        fontSize: 12.5, lineHeight: 1.5, maxHeight: 440, overflow: "auto" }}>{file.data.content}</pre>
    );
  }

  return (
    <div className="report-leaf">
      <Md resolveSrc={(src) => api.artifactVersionRawUrl(pid, aid, vid, src)}>
        {file.data.content}
      </Md>
    </div>
  );
}

function VersionRow(
  { pid, aid, row, current, viewing, onView }:
  { pid: string; aid: string; row: TreeRow; current: string; viewing: string | null; onView: (vid: string | null) => void },
) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["artifact", pid, aid] });
  const revert = useMutation({ mutationFn: () => api.revertArtifact(pid, aid, row.v.id), onSuccess: () => { invalidate(); onView(null); } });
  const archiveToggle = useMutation({
    mutationFn: () => api.archiveArtifactVersion(pid, aid, row.v.id, !row.v.archived),
    onSuccess: invalidate,
  });
  const isCurrent = row.v.id === current;
  const isViewing = row.v.id === viewing;
  const note = row.v.note?.trim() ?? "";

  return (
    <Stack
      gap={4}
      style={{
        padding: `6px 6px 6px ${6 + row.depth * 14}px`,
        borderRadius: 6, opacity: row.v.archived ? 0.5 : 1,
        background: isViewing ? "var(--signal-weak)" : row.onCurrentPath ? "var(--machine-weak)" : "transparent",
        border: isViewing ? "1px solid var(--signal)" : "1px solid transparent",
      }}
    >
      <Group gap={8} wrap="nowrap" align="flex-start">
      <button
        type="button" disabled={!note} onClick={() => setOpen((o) => !o)}
        aria-expanded={note ? open : undefined}
        title={note ? (open ? "Hide the note" : "Show the note") : undefined}
        style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2,
          alignItems: "stretch", textAlign: "left", background: "none", border: "none",
          padding: 0, font: "inherit", color: "inherit", cursor: note ? "pointer" : "default" }}
      >
        <Group gap={6} wrap="nowrap">
          <Text size="xs" c="dimmed" style={{ width: 9, flexShrink: 0 }}>{note ? (open ? "▾" : "▸") : ""}</Text>
          <Text size="sm" fw={isCurrent ? 700 : 500} className="mono">{row.v.id}</Text>
          {isCurrent && <Badge size="xs" color="machine" variant="light">current</Badge>}
          {isViewing && <Badge size="xs" color="signal" variant="light">viewing</Badge>}
          {row.v.archived && <Badge size="xs" color="gray" variant="light">archived</Badge>}
        </Group>
        <Group gap={6} wrap="nowrap" pl={15}>
          <UserChip username={row.v.created_by} />
          <Text size="xs" c="dimmed"><RelTime at={row.v.created_at} /></Text>
        </Group>
      </button>
      <Group gap={4} wrap="nowrap">
        {viewing && (
          isViewing ? (
            <Button size="xs" variant="subtle" color="signal" onClick={() => onView(null)}>
              Back
            </Button>
          ) : (
            <Button size="xs" variant="subtle" onClick={() => isCurrent ? onView(null) : onView(row.v.id)}>
              View
            </Button>
          )
        )}
        {!viewing && !isCurrent && (
          <Button size="xs" variant="subtle" onClick={() => onView(row.v.id)}>
            View
          </Button>
        )}
        {!isCurrent && (
          <Button
            size="xs" variant="subtle" loading={revert.isPending}
            onClick={() => {
              if (window.confirm(`Revert to ${row.v.id}? It becomes the current version.`)) revert.mutate();
            }}
          >
            Revert
          </Button>
        )}
        <Tooltip withArrow label={row.v.archived
          ? "Unarchive — count this version again"
          : "Archive — mark this version as noise. It stays here and stays revertable; it just dims and stops counting."}>
          <ActionIcon
            variant="subtle" size="sm" color="gray"
            aria-label={row.v.archived ? "unarchive version" : "archive version"}
            onClick={() => archiveToggle.mutate()} loading={archiveToggle.isPending}
          >
            {row.v.archived ? "↺" : "🗄"}
          </ActionIcon>
        </Tooltip>
      </Group>
      </Group>
      {note && open && (
        <Text size="xs" c="dimmed" pl={15} style={{ lineHeight: 1.45 }}>{note}</Text>
      )}
    </Stack>
  );
}

function TagEditor({ pid, aid, tags, onUpdate }: { pid: string; aid: string; tags: string[]; onUpdate: () => void }) {
  const [opened, setOpened] = useState(false);
  const [newTag, setNewTag] = useState("");
  const allTags = useQuery({ queryKey: ["artifact-tags", pid], queryFn: () => api.listArtifactTags(pid), enabled: opened });
  const setTags = useMutation({
    mutationFn: (next: string[]) => api.setArtifactTags(pid, aid, next),
    onSuccess: () => { onUpdate(); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't update tags", message: String(e) }),
  });

  const toggle = (tag: string) => {
    const next = tags.includes(tag) ? tags.filter((t) => t !== tag) : [...tags, tag];
    setTags.mutate(next);
  };
  const addNew = () => {
    const t = newTag.trim();
    if (!t || tags.includes(t)) return;
    setTags.mutate([...tags, t]);
    setNewTag("");
  };

  const suggestions = (allTags.data ?? []).filter((t) => !tags.includes(t));

  return (
    <Group gap={6} wrap="wrap" align="center">
      {tags.map((t) => (
        <Badge key={t} size="sm" variant="light" color="grape"
          style={{ cursor: "pointer" }} onClick={() => toggle(t)}
          title="Click to remove">{t}</Badge>
      ))}
      <Popover opened={opened} onChange={setOpened} position="bottom-start" shadow="md" withArrow>
        <Popover.Target>
          <Button size="compact-xs" variant="subtle" color="dimmed" onClick={() => setOpened((o) => !o)}>
            + tag
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap={8} style={{ minWidth: 180 }}>
            {suggestions.length > 0 && (
              <Stack gap={4}>
                <Text size="xs" c="dimmed">Existing tags</Text>
                <Group gap={4} wrap="wrap">
                  {suggestions.map((t) => (
                    <Badge key={t} size="sm" variant="outline" color="grape"
                      style={{ cursor: "pointer" }} onClick={() => { toggle(t); setOpened(false); }}>
                      {t}
                    </Badge>
                  ))}
                </Group>
              </Stack>
            )}
            <Group gap={4} wrap="nowrap">
              <TextInput size="xs" placeholder="New tag…" value={newTag}
                onChange={(e) => setNewTag(e.currentTarget.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { addNew(); setOpened(false); } }} />
              <Button size="xs" variant="light" color="grape" onClick={() => { addNew(); setOpened(false); }}>Add</Button>
            </Group>
          </Stack>
        </Popover.Dropdown>
      </Popover>
    </Group>
  );
}

export default function ArtifactDetail() {
  const { id = "", aid = "" } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [comment, setComment] = useState("");
  const [viewingVersion, setViewingVersion] = useState<string | null>(null);
  const artifact = useQuery({ queryKey: ["artifact", id, aid], queryFn: () => api.getArtifact(id, aid) });
  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["artifact", id, aid] });

  const viewFiles = useQuery({
    queryKey: ["artifact-version-files", id, aid, viewingVersion],
    queryFn: () => api.listArtifactVersionFiles(id, aid, viewingVersion!),
    enabled: !!viewingVersion,
  });

  const archiveArtifact = useMutation({
    mutationFn: (archived: boolean) => api.archiveArtifact(id, aid, archived),
    onSuccess: invalidate,
  });
  const openChat = useMutation({
    mutationFn: () => api.createChat(id, `Edit ${artifact.data?.title || aid}`, [aid]),
    onSuccess: (t) => navigate(`/programs/${id}/chat?c=${t.id}`, { replace: true }),
    onError: (e) => notifications.show({ color: "red", title: "Couldn't open chat", message: String(e) }),
  });
  const addComment = async () => {
    if (!comment.trim()) return;
    try { await api.addArtifactComment(id, aid, comment.trim()); setComment(""); invalidate(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't comment", message: String(e) }); }
  };
  const replyThread = async (tid: string, text: string) => {
    try { await api.addArtifactComment(id, aid, text, tid); invalidate(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't reply", message: String(e) }); }
  };
  const completeThread = async (tid: string) => {
    try { await api.completeArtifactThread(id, aid, tid); invalidate(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't complete", message: String(e) }); }
  };
  const reopenThread = async (tid: string) => {
    try { await api.reopenArtifactThread(id, aid, tid); invalidate(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't reopen", message: String(e) }); }
  };
  const deleteThread = async (tid: string) => {
    try { await api.deleteArtifactThread(id, aid, tid); invalidate(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't delete", message: String(e) }); }
  };
  const seenThread = async (tid: string) => {
    try { await api.seenArtifactThread(id, aid, tid); invalidate(); }
    catch { /* best-effort */ }
  };

  if (artifact.isLoading) return <Loader color="machine" />;
  // A failed poll must NEVER replace loaded content: every query polls on a 10s
  // interval and on window focus (main.tsx), so one blip — a backend restart, a
  // proxy hiccup — used to swap a working page for "not found", and it did not
  // come back on the next successful poll. Only absence means absent.
  if (!artifact.data) {
    return <EmptyState title="Artifact not found">Nothing here at "{aid}". It may have been removed.</EmptyState>;
  }
  const art = artifact.data;

  const chat = liveChatId(art.lock);
  if (chat) return <Navigate to={`/programs/${id}/chat?c=${chat}`} replace />;

  const rows = buildArtifactTree(art.versions, art.current);

  const discard = () => {
    if (!window.confirm("Discard this artifact? It's archived but kept in history — you can un-discard it later.")) return;
    archiveArtifact.mutate(true);
  };
  const undiscard = () => archiveArtifact.mutate(false);

  const showingVersion = viewingVersion ?? art.current;
  const showingFiles = viewingVersion ? (viewFiles.data ?? []) : art.current_files;

  return (
    <Stack gap="lg" style={canvasBreakout}>
      <div>
        <BackLink to={`/programs/${id}/artifacts`}>{program.data?.title || art.program || id} / Artifacts</BackLink>
        <Group justify="space-between" align="flex-start" wrap="nowrap" mt={4}>
          <Group gap={10} align="center" wrap="wrap">
            <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0, lineHeight: 1.25 }}>
              {art.title || art.id}
            </h1>
            <Badge color="machine" variant="light">{art.kind}</Badge>
            {art.archived && <Badge color="gray" variant="light">discarded</Badge>}
          </Group>
          <Group gap={8} wrap="nowrap">
            {art.current && (
              <Button component="a" href={api.artifactDownloadUrl(id, aid, art.current)} variant="default">
                Download
              </Button>
            )}
            <Tooltip label={art.lock.holder_id ? `busy — ${art.lock.holder_id}` : ""} disabled={!art.lock.holder_id} withArrow>
              <span style={{ display: "inline-block" }}>
                <Button
                  variant="default" loading={openChat.isPending}
                  disabled={!!art.lock.holder_id}
                  onClick={() => openChat.mutate()}
                >
                  Open chat
                </Button>
              </span>
            </Tooltip>
            {art.archived ? (
              <Button variant="default" onClick={undiscard} loading={archiveArtifact.isPending}>Un-discard</Button>
            ) : (
              <Button variant="default" color="red" onClick={discard} loading={archiveArtifact.isPending}>Discard</Button>
            )}
          </Group>
        </Group>
        <div style={{ marginTop: 8 }}>
          <TagEditor pid={id} aid={aid} tags={art.tags ?? []} onUpdate={invalidate} />
        </div>
        {art.lock.holder_id && (
          <Card withBorder padding="sm" mt={10} style={{ background: "var(--paper)" }}>
            <Text size="sm">🔒 held by {art.lock.holder_kind} {art.lock.holder_id}</Text>
          </Card>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 300px", gap: 20, alignItems: "start" }}>
        <Card padding="lg" radius="md" style={{
          ...cardStyle,
          ...(viewingVersion ? { border: "2px solid var(--signal)", background: "var(--signal-weak)" } : {}),
        }}>
          {viewingVersion ? (
            <Group gap={8} align="center" mb={10}>
              <div className="eyebrow" style={{ color: "var(--signal)" }}>viewing previous version · {viewingVersion}</div>
              <Badge size="xs" color="signal" variant="light">not current</Badge>
              <Button size="compact-xs" variant="subtle" color="signal" onClick={() => setViewingVersion(null)}>
                Back to current
              </Button>
            </Group>
          ) : (
            <div className="eyebrow" style={{ marginBottom: 10 }}>current version{art.current ? ` · ${art.current}` : ""}</div>
          )}
          {viewingVersion && viewFiles.isLoading ? (
            <Loader size="sm" color="machine" />
          ) : (
            <VersionContent pid={id} aid={aid} kind={art.kind} vid={showingVersion} files={showingFiles} />
          )}
        </Card>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>versions · {rows.length}</div>
          {rows.length ? (
            <Stack gap={2}>
              {rows.map((row) => (
                <VersionRow key={row.v.id} pid={id} aid={aid} row={row} current={art.current}
                  viewing={viewingVersion} onView={setViewingVersion} />
              ))}
            </Stack>
          ) : <Text size="sm" c="dimmed">No versions yet.</Text>}
        </Card>
      </div>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>linked sprints · {art.linked_sprints.length}</div>
        {art.linked_sprints.length ? (
          <Stack gap={6}>
            {art.linked_sprints.map((s) => (
              <Group key={s.id} justify="space-between" wrap="nowrap">
                <Link to={`/sprints/${s.id}`} className="view">
                  {s.title ? `${s.title} · ` : ""}<span className="mono">{s.id}</span>
                </Link>
                <StatusBadge status={s.status} />
              </Group>
            ))}
          </Stack>
        ) : <Text size="sm" c="dimmed">No sprints linked.</Text>}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>comments{art.threads.length ? ` · ${art.threads.length}` : ""}</div>
        <Stack gap={16}>
          {art.threads.length > 0 && (
            <Stack gap={8}>
              {art.threads.map((t) => (
                <FeedbackThread key={t.id} thread={t}
                  onReply={(text) => replyThread(t.id, text)}
                  onComplete={() => completeThread(t.id)}
                  onReopen={() => reopenThread(t.id)}
                  onDelete={() => deleteThread(t.id)}
                  onSeen={() => seenThread(t.id)}
                  respondsNow={false} />
              ))}
            </Stack>
          )}
          <Group gap={8} align="flex-start">
            <Textarea style={{ flex: 1 }} placeholder="Add a comment…"
              autosize minRows={1} value={comment} onChange={(e) => setComment(e.currentTarget.value)} />
            <Button variant="light" color="machine" onClick={addComment}>Send</Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
