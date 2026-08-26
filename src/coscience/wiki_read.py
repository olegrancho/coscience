"""Shaping bundle pages into API payloads. Pure: no IO, no substrate.

Everything here takes already-loaded pages and plain dicts and returns plain
JSON-serialisable data, so the whole read surface is unit-testable without a
substrate on disk. Bundle IO lives in wiki_store; writes live in Service.
"""
from __future__ import annotations

import re

from coscience import wiki_okf
from coscience.frontmatter_io import parse as _parse_frontmatter

TIERS = ("unverified", "machine-confirmed", "human-reviewed")


def trust_tier(page: wiki_okf.Page) -> str:
    """Spec 6.1: trust is *derived* from `verified`, never stored. `status` is the
    orthogonal lifecycle axis — a `stable` page nobody checked is unverified."""
    actors = [str(v.get("by", "")) for v in (page.verified or [])]
    if any(a.startswith("human:") for a in actors):
        return "human-reviewed"
    return "machine-confirmed" if actors else "unverified"


_EXCERPT_PAD = 80


def _excerpt(body: str, needle: str) -> str:
    i = body.lower().find(needle)
    if i < 0:
        return body[:_EXCERPT_PAD * 2].strip()
    start, stop = max(0, i - _EXCERPT_PAD), min(len(body), i + len(needle) + _EXCERPT_PAD)
    return ("…" if start else "") + body[start:stop].strip() + ("…" if stop < len(body) else "")


def search(pages: list[wiki_okf.Page], q: str, limit: int = 50) -> list[dict]:
    """Substring search, scored title > alias > body.

    Crude on purpose: a bundle is hundreds of pages, so an index buys nothing, and
    a ranker we cannot explain is worse than a dumb one we can. A blank query
    returns nothing rather than the whole bundle."""
    needle = (q or "").strip().lower()
    if not needle:
        return []
    hits = []
    for p in pages:
        score = 0
        if needle in (p.title or "").lower():
            score = 3
        elif any(needle in a.lower() for a in p.aliases):
            score = 2
        elif needle in (p.description or "").lower() or needle in p.body.lower():
            score = 1
        if not score:
            continue
        hits.append({"path": p.path, "title": p.title or p.slug, "type": p.type,
                     "trust": trust_tier(p), "score": score,
                     "excerpt": _excerpt(p.body, needle)})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    return hits[:limit]


# The frozen 12 of spec 7, in the order a reader wants them: what this thing IS,
# then what it is part of, then what it needs, then the softer commentary.
_REL_ORDER = ("is_a", "part_of", "requires", "enables", "implements", "exemplifies",
              "measures", "causally_precedes", "contradicts", "refines", "replaces",
              "extends")


def _norm_target(target: str) -> str:
    return (target or "").split("#", 1)[0].strip().lstrip("/")


def provenance_ref(source_id: str, resource: str, program_id: str = "") -> dict:
    """A source's `resource` as a dashboard link. Sources are pointers into the
    platform (spec 5) — this is the payoff for building the wiki inside it.

    Two spellings reach here and both must route. A page may name the platform
    object directly (`/results/r7.md`), or it may name the bundle's own source
    page (`/sources/result-r7.md`) — which is the form the bundle's CLAUDE.md
    template actually shows the agent, and therefore the form every real ingest
    has produced. Routing only the first spelling left every chip on every page
    unclickable while looking exactly like a page that cited nothing.

    Anything unrecognised is reported as `unknown` with an empty href rather than
    dropped: a page citing something we cannot route to is still citing it, and
    hiding that would look like the claim had no source at all."""
    r = (resource or "").strip()
    parts = r.lstrip("/").split("/")
    kind, href = "unknown", ""
    if parts[0] == "results" and len(parts) >= 2:
        kind, href = "result", f"/results/{parts[1].removesuffix('.md')}"
    elif parts[0] == "sprints" and len(parts) >= 2:
        kind, href = "sprint", f"/sprints/{parts[1]}"
    elif parts[0] == "programs" and len(parts) >= 4 and parts[2] == "artifacts":
        kind, href = "artifact", f"/programs/{parts[1]}/artifacts/{parts[3]}"
    elif parts[0] == "sources" and len(parts) >= 2:
        # wiki_store.program_objects names these pages: `sources/result-<rid>.md`
        # and `sources/artifact-<aid>-<vid>.md`. Reverse exactly that spelling.
        stem = parts[1].removesuffix(".md")
        if stem.startswith("result-"):
            kind, href = "result", f"/results/{stem[len('result-'):]}"
        elif stem.startswith("artifact-") and program_id and "-" in stem:
            aid = stem[len("artifact-"):].rsplit("-", 1)[0]
            if aid:
                kind, href = "artifact", f"/programs/{program_id}/artifacts/{aid}"
    return {"id": source_id, "kind": kind, "href": href, "resource": r}


# Footnote definitions (`[^id]: ...`) conventionally sit at the end of a document,
# and the page template ends with `# Human notes` — so the agent's attributions
# land inside the one section it is forbidden to write in. Until the prompt and
# lint stop that at the source, do not hand them to the curation box as if a human
# had typed them: saving over them would silently destroy the page's attributions.
_FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:.*$", re.MULTILINE)


def human_notes(page: wiki_okf.Page) -> str:
    return _FOOTNOTE_DEF.sub("", page.section("Human notes")).strip()


def page_detail(page: wiki_okf.Page, pages: list[wiki_okf.Page],
                program_id: str = "") -> dict:
    known = {p.path: p for p in pages}
    relations = []
    for r in page.relations:
        target = _norm_target(r.target)
        hit = known.get(target)
        relations.append({"type": r.type, "target": target,
                          "title": (hit.title or hit.slug) if hit else "",
                          "exists": hit is not None,
                          "confidence": r.confidence, "source": r.source})
    order = {t: i for i, t in enumerate(_REL_ORDER)}
    relations.sort(key=lambda r: (order.get(r["type"], len(order)), r["target"]))

    backlinks = []
    for other in pages:
        if other.path == page.path:
            continue
        wikilinks = [_norm_target(w) for w in wiki_okf.wikilinks(other.body)]
        # A wikilink may be written bare ([[slug]]) or as a path ([[dir/slug]]),
        # with or without .md — accept every shape, the way lint's autofix does.
        targets = {_norm_target(t) for t in wiki_okf.body_links(other.body)}
        targets |= set(wikilinks) | {f"{w.removesuffix('.md')}.md" for w in wikilinks}
        typed = sorted({r.type for r in other.relations
                        if _norm_target(r.target) == page.path})
        if page.path in targets or typed:
            backlinks.append({"path": other.path, "title": other.title or other.slug,
                              "type": other.type, "typed": typed})
    backlinks.sort(key=lambda b: b["path"])

    return {
        "path": page.path, "slug": page.slug, "type": page.type,
        "title": page.title, "description": page.description,
        "status": page.status, "tags": list(page.tags), "aliases": list(page.aliases),
        "trust": trust_tier(page), "verified": [dict(v) for v in (page.verified or [])],
        "stale_after": page.stale_after, "body": page.body,
        "human_notes": human_notes(page),
        "relations": relations, "backlinks": backlinks,
        "sources": [provenance_ref(s.id, s.resource, program_id) | {"title": s.title}
                    for s in page.sources],
    }


def summary(pages: list[wiki_okf.Page], state: dict, pending: int,
            lint_counts: dict, index_md: str, *,
            wiki_model: str = "", wiki_enabled: bool = True) -> dict:
    counts: dict[str, int] = {}
    trust = dict.fromkeys(TIERS, 0)
    for p in pages:
        counts[p.type] = counts.get(p.type, 0) + 1
        trust[trust_tier(p)] += 1
    return {
        "counts": counts,
        "trust": trust,
        "pages": len(pages),
        "pending": pending,
        "quarantined": list(state.get("quarantined") or []),
        "run": state.get("run") or None,
        "last_run": state.get("last_run") or None,
        "ingests_since_lint": int(state.get("ingests_since_lint", 0) or 0),
        "lint": dict(lint_counts or {}),
        "wiki_model": wiki_model,
        "wiki_enabled": bool(wiki_enabled),
        # Body only. index.md carries OKF frontmatter like every other page, and
        # the browse UI renders this string as markdown: the opening `---` becomes
        # a rule and the YAML lines become a paragraph closed by the second `---`
        # as a setext heading, so the wiki's landing pane opened with "type: Index
        # title: ... okf_version: '0.2'" set as a heading. Nothing renders
        # frontmatter, so nothing should be handed it.
        "index_md": _parse_frontmatter(index_md or "")[1],
    }
