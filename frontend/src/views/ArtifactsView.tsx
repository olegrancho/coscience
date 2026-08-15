import { Badge, Button, Card, Group, Loader, Stack, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ArtifactRow } from "../api";
import { BackLink, EmptyState, ZoomableImg, isImageName, liveChatId } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };
const THUMB_PX = 56;

function ArtifactThumb({ pid, a }: { pid: string; a: ArtifactRow }) {
  const img = (a.files ?? []).find(isImageName);
  if (!img || !a.current) return null;
  return (
    <ZoomableImg src={api.artifactVersionRawUrl(pid, a.id, a.current, img)} alt={img} loading="lazy"
         style={{ width: THUMB_PX, height: THUMB_PX, flexShrink: 0, objectFit: "contain",
                  borderRadius: 4, padding: 2,
                  background: "#fff", border: "1px solid var(--hairline)" }} />
  );
}

function ArtifactCard({ pid, a }: { pid: string; a: ArtifactRow }) {
  const chatId = liveChatId(a.lock);
  const to = chatId ? `/programs/${pid}/chat?c=${chatId}` : `/programs/${pid}/artifacts/${a.id}`;
  const excerpt = a.excerpt?.trim()?.replace(/^#+\s*/gm, "");

  return (
    <Link to={to} style={{ textDecoration: "none", color: "inherit" }}>
      <Card withBorder padding="md" radius="md" style={{ height: "100%", opacity: a.archived ? 0.55 : 1 }}>
        <Group justify="space-between" align="flex-start" wrap="nowrap" gap={8}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Group gap={8} align="center" wrap="nowrap">
              <Text size="sm" fw={600} truncate style={{ minWidth: 0 }}>{a.title || a.id}</Text>
              {a.lock.holder_id && (
                <Badge size="xs" color="signal" variant="light" style={{ flexShrink: 0 }}>
                  {chatId ? "💬 in chat" : "🔒 locked"}
                </Badge>
              )}
            </Group>
            <Group gap={6} wrap="wrap" mt={6}>
              <Badge size="xs" color="machine" variant="light">{a.kind}</Badge>
              {a.archived && <Badge size="xs" color="gray" variant="light">discarded</Badge>}
              {(a.tags ?? []).map((t) => (
                <Badge key={t} size="xs" color="grape" variant="light">{t}</Badge>
              ))}
            </Group>
            <Text size="xs" c="dimmed" mt={8} className="mono">
              {a.current || "—"} · {a.version_count} version{a.version_count === 1 ? "" : "s"}
            </Text>
            {excerpt && (
              <Text size="xs" c="dimmed" mt={6} lineClamp={2}>{excerpt}</Text>
            )}
          </div>
          <ArtifactThumb pid={pid} a={a} />
        </Group>
      </Card>
    </Link>
  );
}

export default function ArtifactsView() {
  const { id = "" } = useParams();
  const [search, setSearch] = useState("");
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  const [showDiscarded, setShowDiscarded] = useState(false);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const artifacts = useQuery({ queryKey: ["artifacts", id, false], queryFn: () => api.listArtifacts(id) });
  const discarded = useQuery({
    queryKey: ["artifacts", id, true],
    queryFn: () => api.listArtifacts(id, true),
    enabled: showDiscarded,
  });
  const allTags = useQuery({ queryKey: ["artifact-tags", id], queryFn: () => api.listArtifactTags(id) });

  if (artifacts.isLoading) return <Loader color="machine" />;
  if (artifacts.error || !artifacts.data) {
    return <EmptyState title="Program not found">Nothing here at "{id}".</EmptyState>;
  }

  const toggleTag = (tag: string) => {
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag); else next.add(tag);
      return next;
    });
  };

  const activeIds = new Set(artifacts.data.map((a) => a.id));
  const discardedOnly = showDiscarded
    ? (discarded.data ?? []).filter((a) => !activeIds.has(a.id))
    : [];

  const lc = search.toLowerCase();
  const applyFilters = (list: ArtifactRow[]) => list.filter((a) => {
    if (search && !(a.title || a.id).toLowerCase().includes(lc)) return false;
    if (activeTags.size > 0 && !(a.tags ?? []).some((t) => activeTags.has(t))) return false;
    return true;
  });

  const filtered = applyFilters(artifacts.data);
  const filteredDiscarded = applyFilters(discardedOnly);

  const title = program.data?.title || id;
  const tags = allTags.data ?? [];

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>{title}</BackLink>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0 }}>Artifacts</h1>
        <Text size="sm" c="dimmed" mt={6} style={{ maxWidth: 640 }}>
          Program deliverables — reports, data, figures, pages. Each artifact is versioned and editable via chat.
        </Text>
      </div>

      {(tags.length > 0 || artifacts.data.length > 3) && (
        <Card padding="md" radius="md" style={cardStyle}>
          <Group gap={10} wrap="wrap" align="center">
            <TextInput size="xs" placeholder="Search artifacts…" value={search}
              onChange={(e) => setSearch(e.currentTarget.value)} style={{ width: 220 }} />
            {tags.length > 0 && (
              <>
                <Text size="xs" c="dimmed">Tags:</Text>
                {tags.map((t) => (
                  <Badge key={t} size="sm"
                    color={activeTags.has(t) ? "grape" : "gray"}
                    variant={activeTags.has(t) ? "filled" : "outline"}
                    style={{ cursor: "pointer" }}
                    onClick={() => toggleTag(t)}>
                    {t}
                  </Badge>
                ))}
                {activeTags.size > 0 && (
                  <button type="button" className="linklike" style={{ fontSize: 12 }}
                    onClick={() => setActiveTags(new Set())}>clear</button>
                )}
              </>
            )}
          </Group>
        </Card>
      )}

      {filtered.length === 0 ? (
        <Text size="sm" c="dimmed">
          {artifacts.data.length === 0 ? "No artifacts yet." : "No artifacts match the current filter."}
        </Text>
      ) : (
        <Stack gap={10}>
          {filtered.map((a) => <ArtifactCard key={a.id} pid={id} a={a} />)}
        </Stack>
      )}

      {showDiscarded && filteredDiscarded.length > 0 && (
        <Stack gap={10}>
          <div className="eyebrow">discarded</div>
          {filteredDiscarded.map((a) => <ArtifactCard key={a.id} pid={id} a={a} />)}
        </Stack>
      )}

      <Button variant="subtle" color="dimmed" size="xs"
        style={{ alignSelf: "flex-start" }}
        loading={showDiscarded && discarded.isLoading}
        onClick={() => setShowDiscarded((v) => !v)}>
        {showDiscarded ? "Hide discarded artifacts" : "Show discarded artifacts"}
      </Button>
    </Stack>
  );
}
