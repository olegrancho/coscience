/** Pure helpers for rendering a wiki page. No React, no fetch — so the link and
 *  outline rules are unit-testable on their own, per the design's §12. */

/** A link the wiki owns, as opposed to one pointing out of the platform. */
export function isInternalLink(href: string): boolean {
  const h = (href || "").trim();
  if (!h || /^[a-z][a-z0-9+.-]*:/i.test(h)) return false;   // any scheme
  return !h.startsWith("//");
}

/** A bundle link as a dashboard route. Bundle links are written
 *  `/concepts/a.md` (phase 1's autofix writes them bundle-absolute); the wiki
 *  view addresses pages by path without the extension. */
export function wikiHref(programId: string, target: string): string {
  const raw = (target || "").trim();
  if (!isInternalLink(raw)) return raw;
  const [pathPart, anchor] = raw.split("#", 2);
  // A bare `#anchor` addresses the page you are already on — GFM footnote markers
  // and their back-references are exactly this shape. Rebuilding a route from the
  // empty path sent every footnote click to the wiki index instead.
  if (!pathPart) return raw;
  const clean = pathPart.replace(/^\//, "").replace(/\.md$/, "");
  const base = `/programs/${programId}/wiki/${clean}`;
  return anchor ? `${base}#${anchor}` : base;
}

export function headingId(text: string): string {
  return (text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export interface OutlineItem { level: number; text: string; id: string }

/** Headings in body order, skipping fenced code. Duplicate texts get -2, -3, …
 *  so an anchor always addresses exactly one heading. */
export function outline(body: string): OutlineItem[] {
  const out: OutlineItem[] = [];
  const seen = new Map<string, number>();
  let fenced = false;
  for (const line of (body || "").split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; continue; }
    if (fenced) continue;
    const m = /^(#{1,6})\s+(.*\S)\s*$/.exec(line);
    if (!m) continue;
    const text = m[2];
    const base = headingId(text);
    const n = (seen.get(base) ?? 0) + 1;
    seen.set(base, n);
    out.push({ level: m[1].length, text, id: n === 1 ? base : `${base}-${n}` });
  }
  return out;
}

export interface FootnoteSource { id: string; resource: string; title: string }

// `[^wt-r1]: sources/result-wt-r1.md` — the label, then whatever it points at.
const FOOTNOTE_DEF = /^(\[\^([^\]\s]+)\]:[ \t]*)(\S.*?)[ \t]*$/;

/**
 * Make footnote definitions lead somewhere.
 *
 * An OKF page cites its sources as GFM footnotes whose *definition* is a bare
 * bundle path. The marker in the prose jumps to the foot of the page correctly
 * — and lands on a line of dead text. The page already carries a `sources`
 * list that knows what each id is called and which bundle page holds it, so
 * the definition can simply be turned into the link it was always describing.
 *
 * A definition that is already a link, or already any other markup, is left
 * exactly as written: the agent may have had a reason.
 */
export function linkFootnotes(body: string, sources: FootnoteSource[]): string {
  const byId = new Map(sources.map((s) => [s.id, s]));
  let fenced = false;
  return (body || "").split("\n").map((line) => {
    if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; return line; }
    if (fenced) return line;
    const m = FOOTNOTE_DEF.exec(line);
    if (!m) return line;
    const [, head, id, rest] = m;
    if (/^[[(<!]/.test(rest)) return line;
    const src = byId.get(id);
    // Fall back to the path the definition already names, so a footnote whose
    // id is missing from `sources` still resolves rather than staying dead.
    const target = src?.resource || (/\.md$/.test(rest) ? rest : "");
    if (!target) return line;
    const text = (src?.title || rest).replace(/([[\]])/g, "\\$1");
    return `${head}[${text}](${target})`;
  }).join("\n");
}
