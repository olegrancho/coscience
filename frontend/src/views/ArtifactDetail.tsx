import { ActionIcon, Badge, Button, Card, Group, Loader, Stack, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import Md from "../components/Md";
import { api } from "../api";
import { buildArtifactTree, type TreeRow } from "../components/artifactTree";
import { BackLink, EmptyState, RelTime } from "../components/ui";
import { UserChip } from "../auth";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/** Renders the artifact's current version, dispatching on `kind`. Text-like
 *  kinds (md/text/data/anything else) read the first current file; figure and
 *  page kinds hit the download/page endpoints directly (no file fetch). */
function CurrentVersion(
  { pid, aid, kind, current, files }:
  { pid: string; aid: string; kind: string; current: string; files: string[] },
) {
  const name = files[0];
  const textLike = kind !== "figure" && kind !== "page";
  const file = useQuery({
    queryKey: ["artifact-file", pid, aid, current, name],
    queryFn: () => api.readArtifactFile(pid, aid, current, name!),
    enabled: textLike && !!current && !!name,
  });

  if (kind === "figure") {
    if (!current) return <Text size="sm" c="dimmed">No content yet.</Text>;
    return <img src={api.artifactDownloadUrl(pid, aid, current)} style={{ maxWidth: "100%" }} alt="" />;
  }

  if (kind === "page") {
    if (!current) return <Text size="sm" c="dimmed">No content yet.</Text>;
    return (
      <iframe
        title="artifact page"
        sandbox="allow-scripts"
        src={api.artifactPageUrl(pid, aid, current, "index.html")}
        style={{ width: "100%", height: 520, border: "1px solid var(--hairline)", borderRadius: 8 }}
      />
    );
  }

  if (!current || !name) return <Text size="sm" c="dimmed">No content yet.</Text>;
  if (file.isLoading) return <Loader size="sm" color="machine" />;
  if (file.error || !file.data) return <Text size="sm" c="red">Couldn't load the file.</Text>;

  if (kind === "data") {
    if (file.data.binary) return <Text size="sm" c="dimmed">Binary — download to view.</Text>;
    return (
      <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
        fontSize: 12.5, lineHeight: 1.5, maxHeight: 440, overflow: "auto" }}>{file.data.content}</pre>
    );
  }

  // md / text (and any other unrecognized kind falls back to markdown rendering)
  return <div className="report-leaf"><Md>{file.data.content}</Md></div>;
}

/** One row in the version-tree sidebar: id, author, age, note, plus a
 *  view/revert action (non-current rows) and an archive/unarchive toggle. */
function VersionRow(
  { pid, aid, row, current }:
  { pid: string; aid: string; row: TreeRow; current: string },
) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["artifact", pid, aid] });
  const revert = useMutation({ mutationFn: () => api.revertArtifact(pid, aid, row.v.id), onSuccess: invalidate });
  const archiveToggle = useMutation({
    mutationFn: () => api.archiveArtifactVersion(pid, aid, row.v.id, !row.v.archived),
    onSuccess: invalidate,
  });
  const isCurrent = row.v.id === current;

  return (
    <Group
      gap={8} wrap="nowrap" align="flex-start"
      style={{
        padding: `6px 6px 6px ${6 + row.depth * 14}px`,
        borderRadius: 6, opacity: row.v.archived ? 0.5 : 1,
        background: row.onCurrentPath ? "var(--machine-weak)" : "transparent",
      }}
    >
      <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
        <Group gap={6} wrap="nowrap">
          <Text size="sm" fw={isCurrent ? 700 : 500} className="mono">{row.v.id}</Text>
          {isCurrent && <Badge size="xs" color="machine" variant="light">current</Badge>}
          {row.v.archived && <Badge size="xs" color="gray" variant="light">archived</Badge>}
        </Group>
        <Group gap={6} wrap="nowrap">
          <UserChip username={row.v.created_by} />
          <Text size="xs" c="dimmed"><RelTime at={row.v.created_at} /></Text>
        </Group>
        {row.v.note && <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>{row.v.note}</Text>}
      </Stack>
      <Group gap={4} wrap="nowrap">
        {!isCurrent && (
          <Button size="xs" variant="subtle" onClick={() => revert.mutate()} loading={revert.isPending}>
            View/Revert
          </Button>
        )}
        <ActionIcon
          variant="subtle" size="sm" color="gray"
          aria-label={row.v.archived ? "unarchive version" : "archive version"}
          onClick={() => archiveToggle.mutate()} loading={archiveToggle.isPending}
        >
          {row.v.archived ? "↺" : "🗄"}
        </ActionIcon>
      </Group>
    </Group>
  );
}

export default function ArtifactDetail() {
  const { id = "", aid = "" } = useParams();
  const qc = useQueryClient();
  const artifact = useQuery({ queryKey: ["artifact", id, aid], queryFn: () => api.getArtifact(id, aid) });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["artifact", id, aid] });
  const archiveArtifact = useMutation({
    mutationFn: (archived: boolean) => api.archiveArtifact(id, aid, archived),
    onSuccess: invalidate,
  });

  if (artifact.isLoading) return <Loader color="machine" />;
  if (artifact.error || !artifact.data) {
    return <EmptyState title="Artifact not found">Nothing here at “{aid}”. It may have been removed.</EmptyState>;
  }
  const art = artifact.data;
  const rows = buildArtifactTree(art.versions, art.current);

  const discard = () => {
    if (!window.confirm("Discard this artifact? It's archived but kept in history — you can un-discard it later.")) return;
    archiveArtifact.mutate(true);
  };
  const undiscard = () => archiveArtifact.mutate(false);

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>{art.program || id}</BackLink>
        <Group justify="space-between" align="flex-start" wrap="nowrap" mt={4}>
          <Group gap={10} align="center" wrap="wrap">
            <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0, lineHeight: 1.25 }}>
              {art.title || art.id}
            </h1>
            <Badge color="machine" variant="light">{art.kind}</Badge>
            {art.archived && <Badge color="gray" variant="light">discarded</Badge>}
          </Group>
          <Group gap={8} wrap="nowrap">
            <Button component="a" href={api.artifactDownloadUrl(id, aid, art.current)} variant="default">
              Download
            </Button>
            {art.archived ? (
              <Button variant="default" onClick={undiscard} loading={archiveArtifact.isPending}>Un-discard</Button>
            ) : (
              <Button variant="default" color="red" onClick={discard} loading={archiveArtifact.isPending}>Discard</Button>
            )}
          </Group>
        </Group>
        {art.lock.holder_id && (
          <Card withBorder padding="sm" mt={10} style={{ background: "var(--paper)" }}>
            <Text size="sm">🔒 held by {art.lock.holder_kind} {art.lock.holder_id}</Text>
          </Card>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 300px", gap: 20, alignItems: "start" }}>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>current version{art.current ? ` · ${art.current}` : ""}</div>
          <CurrentVersion pid={id} aid={aid} kind={art.kind} current={art.current} files={art.current_files} />
        </Card>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>versions · {rows.length}</div>
          {rows.length ? (
            <Stack gap={2}>
              {rows.map((row) => <VersionRow key={row.v.id} pid={id} aid={aid} row={row} current={art.current} />)}
            </Stack>
          ) : <Text size="sm" c="dimmed">No versions yet.</Text>}
        </Card>
      </div>
    </Stack>
  );
}
