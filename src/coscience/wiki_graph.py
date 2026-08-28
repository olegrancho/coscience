"""Wiki concept graph: pure rules over parsed pages.

Parsed pages in, {nodes, edges} out. No IO — the same split as wiki_lint, so
the graph can be tested without a substrate. Service owns the cache and the
filesystem; nothing here touches either."""
from __future__ import annotations

from coscience import wiki_okf, wiki_read

NODE_TYPES = frozenset({"Concept", "Entity", "Synthesis"})


def _node_pages(pages: list[wiki_okf.Page]) -> list[wiki_okf.Page]:
    """Source pages are never nodes (parent spec 2.2); lint rule src/is-concept
    already assumes this, so do not re-derive it here."""
    return [p for p in pages
            if p.type in NODE_TYPES and not p.graph_excluded and not p.bad_yaml]


def _resolve(target: str) -> str:
    """A link target as a bundle-relative page path, or "" if it is not one.
    Mirrors wiki_lint._resolve — same inputs must resolve the same way in both."""
    target = (target or "").split("#", 1)[0].strip()
    if not target or "://" in target or target.startswith("mailto:"):
        return ""
    return target.lstrip("/")


def _edges(pages: list[wiki_okf.Page], known: set[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()          # pairs a typed relation covers
    for p in pages:
        for r in p.relations:
            dst = _resolve(r.target)
            if dst not in known:
                continue
            out.append({"id": f"t:{p.path}->{dst}:{r.type}", "src": p.path, "dst": dst,
                        "type": r.type, "confidence": r.confidence, "source": r.source,
                        "typed": True, "materialized": False})
            seen.add((p.path, dst))
    for p in pages:
        for target in dict.fromkeys(wiki_okf.body_links(p.body)):
            dst = _resolve(target)
            if dst not in known or dst == p.path or (p.path, dst) in seen:
                continue
            seen.add((p.path, dst))
            out.append({"id": f"u:{p.path}->{dst}", "src": p.path, "dst": dst,
                        "type": "", "confidence": "", "source": "",
                        "typed": False, "materialized": False})
    return out


def build(pages: list[wiki_okf.Page]) -> dict:
    node_pages = _node_pages(pages)
    known = {p.path for p in node_pages}
    nodes = [{
        "id": p.path, "slug": p.slug, "title": p.title or p.slug,
        "type": p.type, "status": p.status, "trust": wiki_read.trust_tier(p),
        "in_degree": 0, "out_degree": 0, "orphan": True, "cluster": 0,
    } for p in node_pages]
    return {"nodes": nodes, "edges": _edges(node_pages, known)}
