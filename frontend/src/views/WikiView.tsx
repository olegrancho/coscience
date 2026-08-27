import { Badge, Button, Card, Group, Loader, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { Components } from "react-markdown";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { isInternalLink, outline, wikiHref } from "../components/wikiPage";
import { BackLink, EmptyState, MergePolicySelect, ModelSelect } from "../components/ui";
import { api, type WikiPageRow } from "../api";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

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
  // Saves on change, no Save button — same idiom as the page `status` select in the
  // curation panel. There is nothing to batch it with here.
  const setWikiModel = useMutation({
    mutationFn: (model: string) => api.setProgramWikiModel(id, model),
    onSuccess: invalidate });
  // Same idiom: saves on change, locks during a run — the policy is read when
  // a run collects, same reason the model picker locks (spec §11.3).
  const setMergePolicy = useMutation({
    mutationFn: (policy: string) => api.setWikiMergePolicy(id, policy),
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
          <Group gap={10} wrap="nowrap">
            {/* The model that writes these pages belongs next to the button that
                sets it writing, not only behind program settings. */}
            {s && (
              <ModelSelect value={s.wiki_model} label="wiki model"
                           disabled={!!s.run || setWikiModel.isPending}
                           onChange={(m) => setWikiModel.mutate(m)} />
            )}
            {s && (
              <MergePolicySelect value={s.wiki_merge}
                                  disabled={!!s.run || setMergePolicy.isPending}
                                  onChange={(p) => setMergePolicy.mutate(p)} />
            )}
            <Button size="xs" variant="default" onClick={() => run.mutate("ingest")}
                    disabled={!!s?.run}>Ingest now</Button>
            <Button size="xs" variant="default" onClick={() => run.mutate("lint")}
                    disabled={!!s?.run}>Lint now</Button>
            {/* Same idiom as ProgramDetail's "open wiki →": the destination
                answers "is this wiki healthy", and a badge here says whether
                anything there is waiting on a human. */}
            <Link to={`/programs/${id}/wiki/lint`} className="view" style={{ fontSize: 13 }}>
              maintenance
              {!!s?.merge_proposals && (
                <Badge size="xs" variant="light" color="machine" ml={6}>
                  {s.merge_proposals}
                </Badge>
              )}
            </Link>
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
          {!slug && s && (
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
                <Md components={mdComponents}>{page.data.body}</Md>
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
