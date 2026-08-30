import { Badge, Button, Card, Group, Loader, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { Components } from "react-markdown";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import WikiNeighbourhood from "../components/WikiNeighbourhood";
import { isInternalLink, linkFootnotes, outline, wikiHref } from "../components/wikiPage";
import WikiSettingsModal from "../components/WikiSettingsModal";
import WikiViewSwitch from "../components/WikiViewSwitch";
import { AbsTime, BackLink, EmptyState } from "../components/ui";
import { api, type WikiPageRow } from "../api";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/* The stored values are the vocabulary's, which is right for the format and
   terse to the point of opacity in a panel. */
/* The format's own section name, relabelled for reading. The heading in the
   file stays `# Human notes` — it is protected backend-side, the merge and the
   writer prompt both key on it — but nothing about the reader is what makes
   the section worth having. It is that the notes persist. */
const SECTION_LABEL: Record<string, string> = { "human notes": "Notes" };
const relabel = (text: string) => SECTION_LABEL[text.trim().toLowerCase()] ?? text;

const TRUST_LABEL: Record<string, string> = {
  "unverified": "Unverified",
  "machine-confirmed": "Machine-confirmed",
  "human-reviewed": "Reviewed by a person",
};

const TYPE_ORDER = ["Concept", "Entity", "Synthesis", "Source", "Question"];
// Above this many pages the tree stops being scannable and the groups start shut.
const COLLAPSE_ABOVE = 40;
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
  const [settings, setSettings] = useState(false);

  const summary = useQuery({ queryKey: ["wiki", id],
                             queryFn: () => api.getWikiSummary(id) });
  const pages = useQuery({ queryKey: ["wiki-pages", id],
                           queryFn: () => api.listWikiPages(id) });
  const hits = useQuery({ queryKey: ["wiki-search", id, q], enabled: q.trim().length > 0,
                          queryFn: () => api.searchWiki(id, q) });
  const page = useQuery({ queryKey: ["wiki-page", id, slug], enabled: !!slug,
                          queryFn: () => api.getWikiPage(id, slug) });
  // Findings are recomputed live on every /wiki/lint GET (never autofixed), so
  // this is cheap enough to fetch unconditionally rather than only when a page
  // is open — the strip needs it, but so would a future "clean wiki" indicator.
  const lint = useQuery({ queryKey: ["wiki-lint", id],
                          queryFn: () => api.getWikiLint(id) });

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

  // Every pane that renders bundle markdown needs this, not just the page body:
  // an internal link left to the browser sends it to /concepts/x.md, a route the
  // app does not serve, and the dashboard is gone. index.md is nothing but links,
  // and it is the wiki's landing view — it was the one pane rendering raw <Md>.
  const mdComponents: Components = useMemo(() => ({
    h1: ({ children, ...rest }) => (
      <h1 {...rest}>
        {typeof children === "string" ? relabel(children) : children}
      </h1>
    ),
    a: ({ href, children, ...rest }) => {
      const h = String(href ?? "");
      // A same-page anchor stays a plain <a>: the browser jumps natively, whereas
      // a router <Link> would push a navigation and not scroll at all. Footnote
      // markers and their back-references are all of this shape.
      if (h.startsWith("#")) return <a href={h} {...rest}>{children}</a>;
      return isInternalLink(h)
        ? <Link to={wikiHref(id, h)}>{children}</Link>
        : <a href={h} target="_blank" rel="noreferrer" {...rest}>{children}</a>;
    },
  }), [id]);

  // A flat list is fine at a dozen pages and useless at a few hundred: 140 concepts
  // push entities and sources past the fold, so the only way to reach them is the
  // search box. Below the threshold nothing collapses — a wiki you can see whole
  // should stay whole. Above it, groups start shut except the one you are reading
  // from, and the counts tell you what is behind each.
  const [shut, setShut] = useState<Record<string, boolean>>({});
  const total = (pages.data ?? []).length;
  const currentType = (pages.data ?? [])
    .find((p) => p.path.replace(/\.md$/, "") === slug)?.type ?? "";
  const isGroupOpen = (type: string) =>
    shut[type] === undefined
      ? total <= COLLAPSE_ABOVE || type === currentType
      : !shut[type];
  const toggleGroup = (type: string) =>
    setShut((prev) => ({ ...prev, [type]: isGroupOpen(type) }));

  const s = summary.data;
  // Live findings for the page you are reading only — the maintenance page
  // (WikiLintView) is where "is this wiki healthy" lives; this answers
  // "is this page sound" without leaving it (spec §11.3).
  const pageFindings = slug
    ? (lint.data?.findings ?? []).filter((f) => f.path.replace(/\.md$/, "") === slug)
    : [];
  // Footnote definitions arrive as bare bundle paths, which render as dead
  // text at the foot of the page — the marker jumps there and the trail stops.
  const body = useMemo(
    () => (page.data ? linkFootnotes(page.data.body, page.data.sources) : ""),
    [page.data]);
  // Newest signature wins: `verified` is append-only, so the last entry is the
  // current one.
  const lastVerified = page.data?.verified?.length
    ? page.data.verified[page.data.verified.length - 1] : null;
  const dirty = notes !== null && notes !== (page.data?.human_notes ?? "");
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
        {/* Three kinds of thing used to share one undifferentiated row: two
            select boxes, two action buttons and two links, in that order. They
            are separated now — where you can go, what you can do, and what you
            can configure — because a "Graph" link reading as a sibling of
            "Lint now" is what made it look misplaced. */}
        <Group justify="space-between" align="center" wrap="wrap" gap={12}>
          <Group gap={14} align="center" wrap="nowrap">
            <Text fw={600} size="xl">Wiki</Text>
            <WikiViewSwitch programId={id} current="pages" />
          </Group>
          <Group gap={8} wrap="nowrap">
            {/* wiki.beat() returns early when the wiki is switched off, so
                these do nothing at all for such a program. A button that looks
                live and silently no-ops is worse than one that is out. */}
            <Button size="xs" variant="default" onClick={() => run.mutate("ingest")}
                    disabled={!!s?.run || !s?.wiki_enabled}
                    title={s && !s.wiki_enabled ? "the wiki is switched off" : undefined}>
              Ingest now
            </Button>
            <Button size="xs" variant="default" onClick={() => run.mutate("lint")}
                    disabled={!!s?.run || !s?.wiki_enabled}
                    title={s && !s.wiki_enabled ? "the wiki is switched off" : undefined}>
              Lint now
            </Button>
            {/* The badge stays out here rather than moving into the dialog with
                everything else: unaccepted merges are the one thing behind
                settings that is waiting on a person. */}
            <Button size="xs" variant="default" onClick={() => setSettings(true)}
                    aria-label="wiki settings">
              Settings
              {!!s?.merge_proposals && (
                <Badge size="xs" variant="light" color="machine" ml={6}>
                  {s.merge_proposals}
                </Badge>
              )}
            </Button>
          </Group>
        </Group>
      </div>
      {s && (
        <WikiSettingsModal opened={settings} onClose={() => setSettings(false)}
                           programId={id} summary={s} locked={!!s.run}
                           onSaved={invalidate} />
      )}

      {s && (
        <Group gap={6} wrap="wrap">
          {!s.wiki_enabled && (
            <Badge variant="light" color="gray" title="no wiki runs happen for this program">
              wiki off
            </Badge>
          )}
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
          {s.run && (
            <Badge variant="light" color="machine"
                   title={s.run.forced_by
                     ? `forced by ${s.run.forced_by}`
                     : "scheduled beat"}>running…</Badge>
          )}
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
          {total > 0 && (
            <TextInput size="xs" placeholder="Search the wiki" value={q} mb={8}
                       onChange={(e) => setQ(e.currentTarget.value)} />
          )}
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
              .map(([type, rows]) => {
                const open = isGroupOpen(type);
                return (
                  <div key={type}>
                    <h4>
                      <button type="button" className="wiki-group"
                              aria-expanded={open}
                              onClick={() => toggleGroup(type)}>
                        <span className="wiki-caret" aria-hidden="true">
                          {open ? "▾" : "▸"}
                        </span>
                        {GROUP_LABEL[type] ?? type}
                        <span className="wiki-group-count">{rows.length}</span>
                      </button>
                    </h4>
                    {open && (
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
                    )}
                  </div>
                );
              })
          )}
        </nav>

        <main className="wiki-page">
          {/* A wiki with no pages used to render as an empty card above an
              empty tree — indistinguishable from a broken page, and giving no
              hint that the reason is a switch in Settings. There are two ways
              to have nothing, and they want different answers. */}
          {!slug && s && s.pages === 0 && (
            <Card padding="lg" radius="md" style={cardStyle}>
              {s.wiki_enabled ? (
                <EmptyState title="Nothing written yet">
                  No pages have been written for this program.{" "}
                  {s.pending > 0
                    ? `${s.pending} finished ${s.pending === 1 ? "object is" : "objects are"}
                       waiting to be read — the next wiki beat will pick them up, or press
                       “Ingest now”.`
                    : "Nothing has finished that there would be anything to write about yet."}
                </EmptyState>
              ) : (
                <EmptyState title="The wiki is switched off">
                  No wiki runs happen for this program, so nothing is written and no
                  quota is spent on it.{" "}
                  {s.pending > 0
                    ? `${s.pending} finished ${s.pending === 1 ? "object" : "objects"} would
                       be read if you turned it on.`
                    : ""}
                  {" "}Turn it on under Settings.
                </EmptyState>
              )}
            </Card>
          )}
          {!slug && s && s.pages > 0 && (
            <Card padding="lg" radius="md" style={cardStyle}>
              <div className="eyebrow" style={{ marginBottom: 12 }}>index</div>
              <div className="report-leaf">
                <Md components={mdComponents}>{s.index_md}</Md>
              </div>
            </Card>
          )}
          {slug && page.data && (
            <Card padding="lg" radius="md" style={cardStyle}>
              {/* The page's own subject has to outrank the schema's section names
                  below it; at size="lg" it lost to every "# Definition". */}
              <Text component="h2" fw={600} mb={8}
                    style={{ fontSize: 24, lineHeight: 1.25, letterSpacing: "-.01em" }}>
                {page.data.title}
              </Text>
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

              {pageFindings.length > 0 && (
                // Nothing renders here on a clean page — noise on every page you
                // read defeats the point of a strip that is supposed to be worth
                // glancing at (spec §11.3).
                <Card data-testid="page-findings" padding="sm" radius="md" mb="md"
                      style={{ border: "1px solid var(--signal-line)",
                               background: "var(--signal-weak)" }}>
                  <div className="eyebrow" style={{ marginBottom: 6 }}>findings</div>
                  <Stack gap={4}>
                    {pageFindings.map((f, i) => (
                      <Text key={i} size="xs">
                        <code className="mono" style={{ fontSize: 11 }}>{f.rule}</code>
                        {" — "}{f.message}
                      </Text>
                    ))}
                  </Stack>
                </Card>
              )}

              <div className="report-leaf">
                <Md components={mdComponents}>{body}</Md>
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
                      <a href={`#${h.id}`}>{relabel(h.text)}</a>
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

              {/* Three separate things used to be stacked here with no
                  structure and a raw browser <select> in the middle of them:
                  what the page's trust IS, how to change it, and the notes.
                  Same three, given room and a consistent control set. */}
              <Card padding="md" radius="md" style={cardStyle}>
                <div className="eyebrow" style={{ marginBottom: 10 }}>curation</div>

                <Group justify="space-between" align="center" wrap="nowrap" mb={6}>
                  <Group gap={7} wrap="nowrap">
                    <span className={`wiki-dot wiki-dot--${page.data.trust}`} />
                    <Text size="sm">{TRUST_LABEL[page.data.trust] ?? page.data.trust}</Text>
                  </Group>
                  <Button size="compact-xs" variant="light" color="machine"
                          loading={verify.isPending}
                          disabled={page.data.trust === "human-reviewed"}
                          onClick={() => verify.mutate()}>
                    {page.data.trust === "human-reviewed" ? "Verified" : "Mark verified"}
                  </Button>
                </Group>
                {/* Verification is a signature, not a flag: it should say who
                    and when, or the badge is an assertion with nobody behind it. */}
                {lastVerified ? (
                  <Text size="xs" c="dimmed" mb="md">
                    {lastVerified.by} · <AbsTime at={lastVerified.at} />
                  </Text>
                ) : (
                  <Text size="xs" c="dimmed" mb="md">Nobody has checked this page yet.</Text>
                )}

                <Select
                  size="xs" label="Status" aria-label="status"
                  value={page.data.status || "draft"}
                  data={["draft", "stable", "deprecated"]}
                  allowDeselect={false}
                  disabled={setStatus.isPending}
                  onChange={(v) => v && setStatus.mutate(v)}
                  mb="md"
                />

                {/* "Human notes" is the section's name in the file format and
                    is protected backend-side, but it reads as being about who
                    typed it. What matters here is that these notes survive
                    every rewrite the agent makes — so that is what it says. */}
                <Textarea
                  id="wiki-notes" size="xs" label="Notes" aria-label="notes"
                  description="Yours. A wiki run never rewrites or removes them."
                  autosize minRows={4} mb={8}
                  value={notes ?? page.data.human_notes}
                  onChange={(e) => setNotes(e.currentTarget.value)}
                />
                <Group justify="flex-end" gap={8}>
                  {dirty && (
                    <Button size="compact-xs" variant="subtle" color="gray"
                            onClick={() => setNotes(null)}>Discard</Button>
                  )}
                  <Button size="compact-xs" variant="default"
                          loading={saveNotes.isPending} disabled={!dirty}
                          onClick={() => saveNotes.mutate(notes ?? "")}>
                    {dirty ? "Save notes" : "Saved"}
                  </Button>
                </Group>
              </Card>
            </Stack>
          )}
          {slug && (
            <>
              <div className="eyebrow" style={{ marginBottom: 8 }}>neighbourhood</div>
              {/* pageType lets the pane say *why* a page isn't in the concept
                  graph (a Source, by design) instead of guessing — but only
                  once the page list has actually loaded; before that, pass
                  undefined rather than a premature "" that would misread as
                  "no such page". */}
              <WikiNeighbourhood programId={id} slug={slug}
                                  pageType={pages.data ? currentType : undefined} />
            </>
          )}
        </aside>
      </div>
    </Stack>
  );
}
