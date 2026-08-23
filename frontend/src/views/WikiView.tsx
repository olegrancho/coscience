import { Badge, Button, Card, Group, Loader, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { isInternalLink, outline, wikiHref } from "../components/wikiPage";
import { BackLink, EmptyState } from "../components/ui";
import { api, type WikiPageRow } from "../api";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

const TYPE_ORDER = ["Concept", "Entity", "Synthesis", "Source", "Question"];
const GROUP_LABEL: Record<string, string> = {
  Concept: "Concepts", Entity: "Entities", Synthesis: "Syntheses",
  Source: "Sources", Question: "Questions",
};

function isStale(row: WikiPageRow): boolean {
  if (!row.stale_after) return false;
  const t = Date.parse(row.stale_after);
  return Number.isFinite(t) && t < Date.now();
}

export default function WikiView() {
  const { id = "", "*": splat = "" } = useParams();
  const slug = splat.replace(/\.md$/, "");
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [notes, setNotes] = useState<string | null>(null);

  const summary = useQuery({ queryKey: ["wiki", id],
                             queryFn: () => api.getWikiSummary(id) });
  const pages = useQuery({ queryKey: ["wiki-pages", id],
                           queryFn: () => api.listWikiPages(id) });
  const hits = useQuery({ queryKey: ["wiki-search", id, q], enabled: q.trim().length > 0,
                          queryFn: () => api.searchWiki(id, q) });
  const page = useQuery({ queryKey: ["wiki-page", id, slug], enabled: !!slug,
                          queryFn: () => api.getWikiPage(id, slug) });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["wiki", id] });
    qc.invalidateQueries({ queryKey: ["wiki-pages", id] });
  };
  const refreshPage = () => {
    qc.invalidateQueries({ queryKey: ["wiki-page", id, slug] });
    invalidate();
  };

  const run = useMutation({ mutationFn: (kind: "ingest" | "lint") => api.runWiki(id, kind),
                            onSuccess: invalidate });
  const unquarantine = useMutation({ mutationFn: () => api.unquarantineWiki(id),
                                     onSuccess: invalidate });
  const verify = useMutation({ mutationFn: () => api.verifyWikiPage(id, slug),
                               onSuccess: refreshPage });
  const setStatus = useMutation({
    mutationFn: (status: string) => api.setWikiPageStatus(id, slug, status),
    onSuccess: refreshPage });
  const saveNotes = useMutation({
    mutationFn: (text: string) => api.setWikiHumanNotes(id, slug, text),
    onSuccess: () => { setNotes(null); refreshPage(); } });

  const s = summary.data;
  const grouped = TYPE_ORDER
    .map((t) => [t, (pages.data ?? []).filter((p) => p.type === t)] as const)
    .filter(([, rows]) => rows.length > 0);
  const extra = (pages.data ?? []).filter((p) => !TYPE_ORDER.includes(p.type));

  if (summary.isLoading) return <Loader color="machine" />;
  if (summary.error) {
    return <EmptyState title="No wiki here">Nothing at “{id}”.</EmptyState>;
  }

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>Program</BackLink>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Text fw={600} size="xl">Wiki</Text>
          <Group gap={8}>
            <Button size="xs" variant="default" onClick={() => run.mutate("ingest")}
                    disabled={!!s?.run}>Ingest now</Button>
            <Button size="xs" variant="default" onClick={() => run.mutate("lint")}
                    disabled={!!s?.run}>Lint now</Button>
          </Group>
        </Group>
      </div>

      {s && (
        <Group gap={6} wrap="wrap">
          {TYPE_ORDER.filter((t) => s.counts[t]).map((t) => (
            <Badge key={t} variant="light" color="gray">{t} {s.counts[t]}</Badge>
          ))}
          <Badge variant="light" color="gray">{s.pending} pending</Badge>
          <Badge variant="light" color="gray" title="lint">
            lint {s.lint.error ?? 0}E / {s.lint.warn ?? 0}W
          </Badge>
          {s.last_run && (
            <Badge variant="light" color="gray">
              last: {s.last_run.kind} {s.last_run.status}
            </Badge>
          )}
          {s.run && <Badge variant="light" color="machine">running…</Badge>}
        </Group>
      )}

      {!!s?.quarantined.length && (
        <Card padding="md" radius="md"
              style={{ border: "1px solid var(--signal-line)",
                       background: "var(--signal-weak)" }}>
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm">
              {s.quarantined.length} object(s) quarantined — they stopped being retried
              after repeated failures.
            </Text>
            <Button size="xs" variant="default" onClick={() => unquarantine.mutate()}>
              Retry quarantined
            </Button>
          </Group>
        </Card>
      )}

      <div className="wiki-panes">
        <nav className="wiki-tree">
          <TextInput size="xs" placeholder="Search the wiki" value={q} mb={8}
                     onChange={(e) => setQ(e.currentTarget.value)} />
          {q.trim() ? (
            <ul>
              {(hits.data ?? []).map((h) => (
                <li key={h.path} style={{ flexDirection: "column", alignItems: "start" }}>
                  <Link to={`/programs/${id}/wiki/${h.path.replace(/\.md$/, "")}`}>
                    {h.title}
                  </Link>
                  <span className="wiki-excerpt">{h.excerpt}</span>
                </li>
              ))}
            </ul>
          ) : (
            [...grouped, ...(extra.length ? [["Other", extra] as const] : [])]
              .map(([type, rows]) => (
                <div key={type}>
                  <h4>{GROUP_LABEL[type] ?? type}</h4>
                  <ul>
                    {rows.map((p) => (
                      <li key={p.path}>
                        <span className={`wiki-dot wiki-dot--${p.trust}`}
                              title={p.trust} />
                        <Link to={`/programs/${id}/wiki/${p.path.replace(/\.md$/, "")}`}>
                          {p.title}
                        </Link>
                        {isStale(p) && <span className="wiki-stale" title="stale">⚠</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              ))
          )}
        </nav>

        <main className="wiki-page">
          {!slug && s && (
            <Card padding="lg" radius="md" style={cardStyle}>
              <div className="eyebrow" style={{ marginBottom: 12 }}>index</div>
              <div className="report-leaf"><Md>{s.index_md}</Md></div>
            </Card>
          )}
          {slug && page.data && (
            <Card padding="lg" radius="md" style={cardStyle}>
              <Text component="h2" fw={600} size="lg" mb={6}>{page.data.title}</Text>
              <Group gap={6} mb="sm" wrap="wrap">
                <span className={`wiki-dot wiki-dot--${page.data.trust}`}
                      title={page.data.trust} />
                <Text size="xs" c="dimmed">{page.data.type}</Text>
                {page.data.status && (
                  <Badge size="xs" variant="light" color="gray">{page.data.status}</Badge>
                )}
                {page.data.tags.map((t) => (
                  <Badge key={t} size="xs" variant="light" color="gray">{t}</Badge>
                ))}
              </Group>

              {page.data.sources.length > 0 && (
                <>
                  <div className="eyebrow" style={{ marginBottom: 6 }}>cited from</div>
                  <Group gap={6} mb="md" wrap="wrap">
                    {page.data.sources.map((src) => (
                      // An unroutable source is shown as plain text, never a dead
                      // link: a page citing something we cannot route to is still
                      // citing it, and hiding it would read as "no source".
                      src.href
                        ? <Badge key={src.id} size="sm" variant="light" color="machine"
                                 component={Link} to={src.href}
                                 style={{ cursor: "pointer" }}>
                            {src.id}: {src.title || src.resource}
                          </Badge>
                        : <Badge key={src.id} size="sm" variant="light" color="gray"
                                 title={src.resource}>
                            {src.id}: {src.title || src.resource}
                          </Badge>
                    ))}
                  </Group>
                </>
              )}

              <div className="report-leaf">
                <Md components={{
                  // An internal link must route inside the app; letting the browser
                  // follow /concepts/b.md would leave the dashboard entirely.
                  a: ({ href, children, ...rest }) => (
                    isInternalLink(String(href ?? ""))
                      ? <Link to={wikiHref(id, String(href))}>{children}</Link>
                      : <a href={String(href ?? "")} target="_blank"
                           rel="noreferrer" {...rest}>{children}</a>
                  ),
                }}>{page.data.body}</Md>
              </div>
            </Card>
          )}
        </main>

        <aside className="wiki-side">
          {slug && page.data && (
            <Stack gap="md">
              <div>
                <h4>Outline</h4>
                <ul>
                  {outline(page.data.body).map((h) => (
                    <li key={h.id} style={{ marginLeft: (h.level - 1) * 12 }}>
                      <a href={`#${h.id}`}>{h.text}</a>
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h4>Relations</h4>
                <ul>
                  {page.data.relations.map((r, i) => (
                    <li key={`${r.type}-${r.target}-${i}`}>
                      <code className="mono" style={{ fontSize: 11 }}>{r.type}</code>{" "}
                      {r.exists
                        ? <Link to={wikiHref(id, r.target)}>{r.title || r.target}</Link>
                        : <Text component="span" size="xs" c="dimmed"
                                title="missing target">{r.target} (missing)</Text>}
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h4>Backlinks</h4>
                <ul>
                  {page.data.backlinks.map((b) => (
                    <li key={b.path}>
                      <Link to={wikiHref(id, b.path)}>{b.title}</Link>
                      {b.typed.length > 0 && (
                        <Text component="span" size="xs" c="dimmed">
                          ({b.typed.join(", ")})
                        </Text>
                      )}
                    </li>
                  ))}
                </ul>
              </div>

              <Card padding="md" radius="md" style={cardStyle}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>trust</div>
                <Group gap={6} mb={8}>
                  <span className={`wiki-dot wiki-dot--${page.data.trust}`} />
                  <Text size="sm">{page.data.trust}</Text>
                </Group>
                <Button size="xs" variant="default" mb={10}
                        onClick={() => verify.mutate()}>Mark verified</Button>

                {/* A raw select on purpose: ProgramDetail's status filter uses the
                    same idiom, and Mantine's Select is not a native <select>. */}
                <label htmlFor="wiki-status" className="eyebrow"
                       style={{ display: "block", marginBottom: 4 }}>status</label>
                <select id="wiki-status" className="mono"
                        value={page.data.status || "draft"}
                        onChange={(e) => setStatus.mutate(e.currentTarget.value)}>
                  <option value="draft">draft</option>
                  <option value="stable">stable</option>
                  <option value="deprecated">deprecated</option>
                </select>

                <label htmlFor="wiki-notes" className="eyebrow"
                       style={{ display: "block", margin: "10px 0 4px" }}>
                  human notes
                </label>
                <Textarea id="wiki-notes" autosize minRows={4} mb={8}
                          value={notes ?? page.data.human_notes}
                          onChange={(e) => setNotes(e.currentTarget.value)} />
                <Button size="xs" variant="default"
                        onClick={() => saveNotes.mutate(notes ?? page.data.human_notes)}>
                  Save notes
                </Button>
              </Card>
            </Stack>
          )}
        </aside>
      </div>
    </Stack>
  );
}
