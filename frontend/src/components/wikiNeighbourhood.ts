import type { WikiGraphT } from "../api";

/** The centre plus everything within `hops` edges, direction ignored.
 *  Serves both the browse view's pane and the full view's focus mode, so the
 *  two can never disagree about what a neighbourhood is. */
export function neighbourhood(g: WikiGraphT, centreId: string, hops: number): WikiGraphT {
  if (!g.nodes.some((n) => n.id === centreId)) return { nodes: [], edges: [] };
  let reached = new Set([centreId]);
  for (let i = 0; i < hops; i++) {
    const next = new Set(reached);
    for (const e of g.edges) {
      if (reached.has(e.src)) next.add(e.dst);
      if (reached.has(e.dst)) next.add(e.src);
    }
    reached = next;
  }
  return {
    nodes: g.nodes.filter((n) => reached.has(n.id)),
    edges: g.edges.filter((e) => reached.has(e.src) && reached.has(e.dst)),
  };
}

/** Inverts wiki_store's Source-page naming so a Source page's pane can look up
 *  what cites it. `sources/result-<id>.md` came from `result:<id>`;
 *  `sources/artifact-<aid>-<vid>.md` came from `artifact:<aid>@<vid>` — the
 *  version id is the LAST `-`-separated segment, so an artifact id containing
 *  its own hyphens still splits correctly. `slug` is the bare filename stem
 *  (no directory, no `.md`), matching `wiki_okf.Page.slug`. Anything not
 *  shaped like a source page's slug returns "" rather than guessing. */
export function oidForSourceSlug(slug: string): string {
  if (slug.startsWith("result-")) return `result:${slug.slice("result-".length)}`;
  if (slug.startsWith("artifact-")) {
    const rest = slug.slice("artifact-".length);
    const cut = rest.lastIndexOf("-");
    if (cut <= 0 || cut === rest.length - 1) return "";
    return `artifact:${rest.slice(0, cut)}@${rest.slice(cut + 1)}`;
  }
  return "";
}

/** The longest prefix of `text` that fits `avail` px, ellipsised when cut.
 *
 *  SVG has no text overflow: a `<text>` runs past the viewBox and is simply
 *  clipped mid-glyph, so a pane this narrow has to decide where to cut before
 *  it draws. `charPx` is the average advance of the UI sans at the label size
 *  — an estimate, deliberately: measuring text needs a canvas or a layout
 *  pass, and being a character out on a rail this wide is not worth either.
 *  Returns "" when not even two characters fit, so the caller can draw a bare
 *  disc rather than a lone ellipsis. The untruncated title stays on the disc's
 *  `title` attribute, so nothing is lost — only deferred to a hover. */
export function fitLabel(text: string, avail: number, charPx: number): string {
  const max = Math.floor(avail / charPx);
  if (max < 2) return "";
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}
