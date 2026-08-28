"""Wiki concept graph: pure rules over parsed pages.

Parsed pages in, {nodes, edges} out. No IO — the same split as wiki_lint, so
the graph can be tested without a substrate. build() and everything above it
stay pure; cached_build() at the bottom is the sole exception, memoising
build() in the bundle's .wiki/graph.json."""
from __future__ import annotations

import hashlib
import json

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


def _materialized(edges: list[dict]) -> list[dict]:
    """Parent spec 7: `contradicts` is symmetric in meaning but stored on one
    side only. Materialize the reverse for display — and mark it, because it
    must not count toward any metric (design 2)."""
    have = {(e["src"], e["dst"]) for e in edges if e["type"] == "contradicts"}
    out = []
    for e in edges:
        if e["type"] != "contradicts" or (e["dst"], e["src"]) in have:
            continue
        out.append({"id": f"m:{e['dst']}->{e['src']}:contradicts",
                    "src": e["dst"], "dst": e["src"], "type": "contradicts",
                    "confidence": e["confidence"], "source": e["source"],
                    "typed": True, "materialized": True})
    return out


def _clusters(node_ids: list[str], edges: list[dict]) -> dict[str, int]:
    """Connected components, direction ignored. Union-find keyed by the node
    order given, so ids are deterministic for a given page order."""
    parent = {nid: nid for nid in node_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        a, b = find(e["src"]), find(e["dst"])
        if a != b:
            parent[a] = b
    order: dict[str, int] = {}
    return {nid: order.setdefault(find(nid), len(order)) for nid in node_ids}


def _apply_metrics(nodes: list[dict], edges: list[dict]) -> None:
    """Degree and orphan count REAL edges only — materialized reverses are
    display artefacts, and counting them doubles one disagreement."""
    real = [e for e in edges if not e["materialized"]]
    by_id = {n["id"]: n for n in nodes}
    for e in real:
        if e["src"] in by_id:
            by_id[e["src"]]["out_degree"] += 1
        if e["dst"] in by_id:
            by_id[e["dst"]]["in_degree"] += 1
    cluster = _clusters([n["id"] for n in nodes], real)
    for n in nodes:
        n["orphan"] = (n["in_degree"] + n["out_degree"]) == 0
        n["cluster"] = cluster[n["id"]]


def build(pages: list[wiki_okf.Page]) -> dict:
    node_pages = _node_pages(pages)
    known = {p.path for p in node_pages}
    nodes = [{
        "id": p.path, "slug": p.slug, "title": p.title or p.slug,
        "type": p.type, "status": p.status, "trust": wiki_read.trust_tier(p),
        "in_degree": 0, "out_degree": 0, "orphan": True, "cluster": 0,
    } for p in node_pages]
    edges = _edges(node_pages, known)
    edges += _materialized(edges)
    _apply_metrics(nodes, edges)
    return {"nodes": nodes, "edges": edges}


def digest(pages: list[wiki_okf.Page]) -> str:
    """Content hash over every page's path and body-plus-frontmatter.

    The parent spec 10 keys this on (path, mtime, size). Content is used
    instead (design 8.1): mtime churns on every git checkout while content does
    not, and content can change while size does not — that second case serves a
    stale graph. Rebuild is milliseconds at this scale, so correctness is free."""
    h = hashlib.sha256()
    for p in sorted(pages, key=lambda x: x.path):
        h.update(p.path.encode())
        h.update(b"\0")
        h.update(wiki_okf.render_page(p).encode())
        h.update(b"\0")
    return h.hexdigest()


def cached_build(substrate, program_id: str) -> dict:
    """build() over the program's bundle, memoised in .wiki/graph.json.

    Every failure path rebuilds rather than raising: a graph that dies on a
    corrupt cache is a graph that breaks exactly when someone is debugging."""
    from coscience import wiki_store
    pages = wiki_store.iter_pages(substrate, program_id)
    key = digest(pages)
    cache = wiki_store.state_dir(substrate, program_id) / "graph.json"
    try:
        blob = json.loads(cache.read_text())
        if blob.get("key") == key:
            return blob["graph"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    graph = build(pages)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "graph": graph}))
    except OSError:
        pass          # an unwritable cache must not fail the request
    return graph
