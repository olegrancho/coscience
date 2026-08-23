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
