import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { neighbourhood, oidForSourceSlug, fitLabel } from "./wikiNeighbourhood";
import { radialLayout } from "./radialLayout";
import { rimSegment, segmentPath } from "./discGeometry";
import { nodeStyle, nodeSize } from "./wikiGraphStyle";
import { panBy, toWorld, viewBox, zoomAt, zoomOf, type View } from "./svgViewport";

// The pane fills the 320px side rail. The ring is an ellipse, not a circle:
// a circle of the radius the old 240-box used put every node's centre ON the
// box edge, clipping half of each disc, and left no room beside them for a
// name. Squashing it horizontally and using the rail's full height gives the
// labels their room without shrinking the picture.
const BOX_W = 300, BOX_H = 250;
const CX = BOX_W / 2, CY = BOX_H / 2;
const RING_X = 54, RING_Y = 84;

// Annotation. A disc says nothing about which page it is, and the reader
// should not have to hover every one to find out.
const LABEL_PX = 9;      // font-size
const CHAR_PX = 5;       // average advance at LABEL_PX, for fitLabel
const LABEL_GAP = 5;     // rim → text
const EDGE_PAD = 3;      // text → box edge
// Within this much of the vertical axis a node counts as top/bottom: its
// label goes above or below the disc, where there is width to spare, rather
// than out to a side that has almost none.
const UPRIGHT = RING_X * 0.4;

const BASE: View = { x: 0, y: 0, w: BOX_W, h: BOX_H };
// One wheel notch. Small enough that a trackpad's many small deltas do not
// shoot straight to the limit.
const WHEEL_STEP = 1.12;

/** The browse view's neighbourhood pane. Deliberately laid out with
 *  `radialLayout`, never `forceLayout` — a one-hop neighbourhood is under ten
 *  nodes, and keeping d3-force off the browse view's bundle is the whole
 *  reason the radial layout exists (design 6).
 *
 *  `pageType` is optional and comes from data the caller (WikiView) already
 *  has loaded for its own page tree — passing it costs no extra fetch. It
 *  lets the "not in the graph" message be honest about *why*:
 *    - "Source" (a real page, just a type the concept graph excludes by
 *      design) gets the specific, confident explanation.
 *    - `""` (the caller looked and found no page at that address at all)
 *      gets a "no such page" message instead.
 *    - `undefined` (the caller didn't look, e.g. this component used on its
 *      own) falls back to a message that doesn't assert a reason it can't
 *      know — better silent about the cause than confidently wrong. */
export default function WikiNeighbourhood(
  { programId, slug, pageType }: { programId: string; slug: string; pageType?: string },
) {
  const nav = useNavigate();
  // `null` means the reader has not moved the view, so "reset" can be offered
  // only when there is something to reset.
  const [view, setView] = useState<View | null>(null);
  const [hover, setHover] = useState("");
  // A callback ref, not a plain one: the pane returns null while the graph
  // loads, so a mount-time effect would attach its wheel listener to an SVG
  // that does not exist yet and never get another chance.
  const [svgEl, setSvgEl] = useState<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const v = view ?? BASE;
  const vRef = useRef(v);
  vRef.current = v;

  // React routes wheel through a passive listener on the root, so an onWheel
  // prop cannot preventDefault and the page scrolls out from under the pane.
  // A non-passive native listener is the only way to keep the gesture here.
  useEffect(() => {
    const el = svgEl;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const { sx, sy } = toWorld(vRef.current, rect);
      const px = vRef.current.x + (e.clientX - rect.left) * sx;
      const py = vRef.current.y + (e.clientY - rect.top) * sy;
      const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
      setView(zoomAt(vRef.current, px, py, factor, BASE));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [svgEl]);

  const onPointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    drag.current = { x: e.clientX, y: e.clientY };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }, []);
  const onPointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    const from = drag.current;
    if (!from) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const { sx, sy } = toWorld(vRef.current, rect);
    setView(panBy(vRef.current, (e.clientX - from.x) * sx, (e.clientY - from.y) * sy));
    drag.current = { x: e.clientX, y: e.clientY };
  }, []);
  const endDrag = useCallback(() => { drag.current = null; }, []);

  const q = useQuery({ queryKey: ["wiki-graph", programId],
                       queryFn: () => api.getWikiGraph(programId) });
  const centreId = useMemo(
    () => q.data?.nodes.find((n) => n.id.replace(/\.md$/, "") === slug)?.id ?? "",
    [q.data, slug]);
  const sub = useMemo(
    () => (q.data && centreId ? neighbourhood(q.data, centreId, 1) : { nodes: [], edges: [] }),
    [q.data, centreId]);
  const placed = useMemo(
    () => radialLayout(centreId, sub.nodes.map((n) => ({
      id: n.id, data: { label: n.title, stage: "", kind: "", status: n.status },
      position: { x: 0, y: 0 }, style: {},
    })), RING_X, RING_Y),
    [sub, centreId]);
  // Only a slug shaped like a Source page's (`result-<id>`, `artifact-<aid>-<vid>`)
  // has an object id to look citations up for; anything else — including "no
  // page here at all" — has none, so the query is skipped rather than firing a
  // request that citing_pages would answer empty anyway. `slug` here is a page
  // ADDRESS (bundle path minus `.md`, e.g. "sources/result-wt-r1"), so
  // oidForSourceSlug — which expects the bare filename stem — gets only the
  // last path segment.
  const oid = oidForSourceSlug(slug.split("/").pop() ?? "");
  const cites = useQuery({
    queryKey: ["wiki-citations", programId, slug],
    queryFn: () => api.getWikiCitations(programId, oid),
    enabled: !centreId && !!oid,
  });

  if (q.isLoading) return null;
  // A fetch error is not evidence the page is absent from the graph — it's
  // evidence the graph never loaded at all, so the pane must not claim a data
  // fact ("not in the concept graph") it has no basis for.
  if (q.isError) {
    return <p className="muted" style={{ fontSize: 12 }}>Could not load the concept graph.</p>;
  }
  if (!centreId) {
    const message = pageType === "Source"
      ? "This is a Source page — sources are excluded from the concept graph by design."
      : pageType === ""
      ? "No page was found at this address."
      : "This page is not in the concept graph.";
    return (
      <div>
        <p className="muted" style={{ fontSize: 12 }}>{message}</p>
        {cites.data && cites.data.length > 0 && (
          <div>
            <p className="muted" style={{ fontSize: 12, marginBottom: 4 }}>cited by:</p>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {cites.data.map((c) => (
                <li key={c.path} style={{ fontSize: 12 }}>
                  <Link to={`/programs/${programId}/wiki/${c.path.replace(/\.md$/, "")}`} className="view">{c.title}</Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  const byId = new Map(sub.nodes.map((n) => [n.id, n]));

  // Built here rather than inline so it can be drawn after every node: an SVG
  // has no z-index, only document order.
  const hoverNode = hover ? byId.get(hover) : undefined;
  const hoverAt = hover ? placed.find((p) => p.id === hover) : undefined;
  let hoverLabel = null;
  if (hoverNode && hoverAt) {
    const w = hoverNode.title.length * CHAR_PX + 8;
    const cy = hoverAt.position.y + CY;
    // Above the disc normally, below it for a node near the top, so the plate
    // never leaves the picture.
    const top = cy < BOX_H / 3;
    const y = top
      ? cy + nodeSize(hoverNode) / 2 + LABEL_GAP
      : cy - nodeSize(hoverNode) / 2 - LABEL_GAP - 14;
    const x = Math.min(BOX_W - EDGE_PAD - w, Math.max(EDGE_PAD, hoverAt.position.x + CX - w / 2));
    hoverLabel = (
      <g style={{ pointerEvents: "none" }}>
        <rect x={x} y={y} width={w} height={14} rx={3}
              fill="var(--card, #fff)" stroke="var(--hairline, #dfe4e1)" />
        <text x={x + 4} y={y + 10} fontSize={LABEL_PX} fill="var(--ink, #222)">
          {hoverNode.title}
        </text>
      </g>
    );
  }

  return (
    <div>
      <svg ref={setSvgEl} role="img" aria-label="neighbourhood"
           width="100%" height={BOX_H} viewBox={viewBox(v)}
           className="wiki-nbhd"
           onPointerDown={onPointerDown} onPointerMove={onPointerMove}
           onPointerUp={endDrag} onPointerCancel={endDrag} onPointerLeave={endDrag}>
        {sub.edges.map((e) => {
          const a = placed.find((p) => p.id === e.src);
          const b = placed.find((p) => p.id === e.dst);
          const na = byId.get(e.src), nb = byId.get(e.dst);
          if (!a || !b || !na || !nb) return null;
          // Trimmed to the rims, like the full graph's edges: an untrusted
          // page's disc is drawn unfilled, so an untrimmed line runs visibly
          // straight through it.
          const seg = rimSegment(
            a.position.x + CX, a.position.y + CY, b.position.x + CX, b.position.y + CY,
            nodeSize(na) / 2, nodeSize(nb) / 2);
          if (!seg) return null;
          return <path key={e.id} d={segmentPath(seg)} fill="none"
                       stroke="#8a8f98" strokeWidth={1} />;
        })}
        {placed.map((p) => {
          const n = byId.get(p.id);
          if (!n) return null;
          const st = nodeStyle(n, "structure");
          const r = nodeSize(n) / 2;
          const cx = p.position.x + CX, cy = p.position.y + CY;
          const isCentre = p.id === centreId;
          // Labels go outward from the ring so they never cross a spoke: to
          // the right of a node on the right, to the left of one on the left,
          // and above/below the ones near the vertical axis. The centre's own
          // label sits under its disc, where the whole width is free.
          const upright = isCentre || Math.abs(p.position.x) < UPRIGHT;
          const anchor = upright ? "middle" : p.position.x > 0 ? "start" : "end";
          const above = !isCentre && upright && p.position.y < 0;
          const tx = upright ? cx : p.position.x > 0 ? cx + r + LABEL_GAP : cx - r - LABEL_GAP;
          const ty = upright
            ? (above ? cy - r - LABEL_GAP : cy + r + LABEL_GAP)
            : cy;
          const avail = upright
            ? 2 * Math.min(tx, BOX_W - tx) - 2 * EDGE_PAD
            : p.position.x > 0 ? BOX_W - EDGE_PAD - tx : tx - EDGE_PAD;
          const label = fitLabel(n.title, avail, CHAR_PX);
          // A `title` attribute on the circle itself, not a nested `<title>`
          // child — byTitle's svg-title match requires the `<title>` to be a
          // DIRECT child of `<svg>`, which a per-node label nested inside a
          // `<circle>` never is (see WikiGraphView.tsx for the same fix).
          const href = `/programs/${programId}/wiki/${p.id.replace(/\.md$/, "")}`;
          const disc = (
            <>
              <circle cx={cx} cy={cy} r={r}
                      fill={st.background === "transparent" ? "none" : st.background}
                      stroke={st.borderColor}
                      strokeWidth={isCentre ? 3 : hover === p.id ? 3 : 2}
                      {...{ title: n.title }} />
              {label && (
                <text x={tx} y={ty} textAnchor={anchor}
                      dy={upright ? (above ? "0" : "0.8em") : "0.32em"}
                      fontSize={LABEL_PX} fill="var(--ink-muted, #5e6e69)"
                      fontWeight={isCentre || hover === p.id ? 600 : 400}
                      style={{ pointerEvents: "none" }}
                      textDecoration={n.status === "deprecated" ? "line-through" : undefined}>
                  {label}
                </text>
              )}
            </>
          );
          return (
            <g key={p.id} onPointerEnter={() => setHover(p.id)}
               onPointerLeave={() => setHover((h) => (h === p.id ? "" : h))}>
              {isCentre ? disc : (
                // A real <a>, so the destination shows in the status bar and
                // middle-click opens a tab; the click itself is routed rather
                // than reloading the app. The centre is the page you are
                // already on, so it is not a link.
                <a href={href} onClick={(e) => { e.preventDefault(); nav(href); }}
                   style={{ cursor: "pointer" }}>
                  {disc}
                </a>
              )}
            </g>
          );
        })}
        {/* The full title of whatever is under the pointer, drawn last so it
            sits over everything. Zooming cannot reveal a truncated name — the
            text magnifies with the picture — so this is what the truncation
            defers to. */}
        {hoverLabel}
      </svg>
      <div className="wiki-nbhd-foot">
        <Link to={`/programs/${programId}/wiki/graph?focus=${slug}`}
              className="view" style={{ fontSize: 12 }}>
          open full graph ↗
        </Link>
        {view && (
          <button type="button" className="linklike" onClick={() => setView(null)}>
            reset view
          </button>
        )}
      </div>
    </div>
  );
}
