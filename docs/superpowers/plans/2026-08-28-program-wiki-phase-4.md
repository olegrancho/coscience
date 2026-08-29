# Program Wiki Phase 4 — Concept Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the wiki concept graph — a pure builder, an endpoint, a full-page force-directed view, a neighbourhood pane in the browse view, and provenance backlinks from results and artifacts to the pages citing them.

**Architecture:** `wiki_graph.py` is pure (parsed pages in, `{nodes, edges}` out) and testable without a substrate, mirroring `wiki_lint.lint()`. A thin `Service` method adds caching and IO. The frontend adds `forceLayout` and `radialLayout` beside the existing dagre `layout()` in `graphLayout.ts`, so `LineageGraph` is untouched. The full-page view lazy-loads `d3-force`; the neighbourhood pane deliberately does not, using deterministic radial placement instead.

**Tech Stack:** Python 3.12 (backend), FastAPI, pytest. React + TypeScript, `@xyflow/react` (already present), `d3-force` (new), vitest + `@testing-library/react`.

**Spec:** `docs/superpowers/specs/2026-08-28-program-wiki-graph-ui-design.md`, which refines `docs/superpowers/specs/2026-08-20-program-wiki-design.md` (**binding parent** — where the two disagree the parent wins, except the three amendments in the design's §8).

## Global Constraints

- **Two repos.** This repo is CODE. Programs/sprints/results/wiki bundles live in the **substrate**, a separate git repo at `$COSCIENCE_REPO`. Never write test data into a real substrate.
- **`python` is not on PATH.** Use `~/venvs/coscience/bin/python -m pytest`.
- **Frontend tests:** `~/venvs/coscience/bin/python` is irrelevant; use `cd frontend && npx vitest run <path>`.
- **`@testing-library/user-event` is NOT a dependency.** Use `fireEvent`.
- **Scope frontend assertions.** Query by role, title, or a scoped element — never a bare `getByText` in a view full of counts.
- **The unit suite never calls a live LLM.** Nothing in this phase launches an agent; if a test can reach `agent.launch`, it is wrong.
- **Frozen vocabularies.** 12 relation types (`wiki_okf.RELATION_TYPES`), 5 page types (`wiki_okf.PAGE_TYPES`). Adding one is a spec change.
- **Linux-only runtime.** Python ≥3.11.
- **Never commit or push without explicit human approval.** Stage explicit paths; **never `git add -A`**.
- **Commit steps in this plan stage explicit paths** and assume approval was already given for the phase. If it was not, stop and ask.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/coscience/wiki_graph.py` (new) | Pure graph builder: nodes, edges, metrics. No IO. |
| `src/coscience/service.py` (modify) | `wiki_graph()` — cache read/write + delegate; `accept_wiki_merge()` — audit entry. |
| `src/coscience/http_api.py` (modify) | `GET /wiki/graph`, `GET /wiki/citations/{oid}`. |
| `src/coscience/wiki_read.py` (modify) | `citing_pages()` — reverse provenance lookup. |
| `frontend/src/api.ts` (modify) | `WikiGraph` types + `getWikiGraph`, `getWikiCitations`. |
| `frontend/src/components/graphLayout.ts` (modify) | Add `forceLayout`, `radialLayout`. Leave `layout()` alone. |
| `frontend/src/components/wikiGraphStyle.ts` (new) | Encoding: hue by type, fill by trust, size by degree, edge families. Pure, unit-testable. |
| `frontend/src/views/WikiGraphView.tsx` (new) | Full-page view: filters, lens, focus mode. Lazy-loaded. |
| `frontend/src/components/wikiGraphFilter.ts` (new) | `applyFilters` — hides without re-layout, never recomputes `orphan`. Pure. |
| `frontend/src/components/wikiNeighbourhood.ts` (new) | `neighbourhood()` reduction, shared by the pane and focus mode. Pure. |
| `frontend/src/components/WikiNeighbourhood.tsx` (new) | Neighbourhood pane for `WikiView`'s aside. No d3-force. |
| `frontend/src/App.tsx` (modify) | Route `/programs/:id/wiki/graph` **before** `/wiki/*`. |
| `frontend/src/views/WikiView.tsx` (modify) | Mount the neighbourhood pane in `.wiki-side`. |
| `frontend/src/views/SprintDetail.tsx`, `ArtifactDetail.tsx` (modify) | Backlink chips. |

---

## Task 1: Graph builder — nodes and edges

**Files:**
- Create: `src/coscience/wiki_graph.py`
- Test: `tests/test_wiki_graph.py`

**Interfaces:**
- Consumes: `wiki_okf.Page` (fields `path`, `type`, `title`, `status`, `graph_excluded`, `relations`, `body`), `wiki_okf.body_links(body) -> list[str]`, `wiki_read.trust_tier(page) -> str`.
- Produces: `build(pages: list[Page]) -> dict` returning `{"nodes": [...], "edges": [...]}`. Node keys: `id, slug, title, type, status, trust, in_degree, out_degree, orphan, cluster`. Edge keys: `id, src, dst, type, confidence, source, typed, materialized`. Also `NODE_TYPES: frozenset`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_graph.py
from coscience import wiki_graph, wiki_okf


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 300 + "\n")
    return wiki_okf.Page(path=path, **kw)


def _ids(items):
    return sorted(i["id"] for i in items)


def test_only_concept_entity_synthesis_become_nodes():
    pages = [
        _page("concepts/a.md"),
        _page("entities/b.md", type="Entity"),
        _page("syntheses/c.md", type="Synthesis"),
        _page("sources/d.md", type="Source"),
        _page("questions/e.md", type="Question"),
    ]
    g = wiki_graph.build(pages)
    assert _ids(g["nodes"]) == ["concepts/a.md", "entities/b.md", "syntheses/c.md"]


def test_graph_excluded_page_is_not_a_node():
    pages = [_page("concepts/a.md"), _page("concepts/b.md", graph_excluded=True)]
    g = wiki_graph.build(pages)
    assert _ids(g["nodes"]) == ["concepts/a.md"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_graph'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coscience/wiki_graph.py
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


def build(pages: list[wiki_okf.Page]) -> dict:
    nodes = [{
        "id": p.path,
        "slug": p.slug,
        "title": p.title or p.slug,
        "type": p.type,
        "status": p.status,
        "trust": wiki_read.trust_tier(p),
        "in_degree": 0,
        "out_degree": 0,
        "orphan": True,
        "cluster": 0,
    } for p in _node_pages(pages)]
    return {"nodes": nodes, "edges": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing edge test**

```python
def test_typed_edges_come_from_relations_and_carry_their_metadata():
    rel = wiki_okf.Relation(type="refines", target="/concepts/b.md",
                            confidence="high", source="wt-r1")
    pages = [_page("concepts/a.md", relations=[rel],
                   body="# D\n\nSee [b](/concepts/b.md).\n" + "x" * 300),
             _page("concepts/b.md")]
    g = wiki_graph.build(pages)
    typed = [e for e in g["edges"] if e["typed"]]
    assert len(typed) == 1
    e = typed[0]
    assert (e["src"], e["dst"], e["type"]) == ("concepts/a.md", "concepts/b.md", "refines")
    assert (e["confidence"], e["source"]) == ("high", "wt-r1")


def test_untyped_body_link_becomes_an_edge_unless_a_typed_relation_covers_it():
    # a -> b is covered by a relation; a -> c is a bare body link.
    rel = wiki_okf.Relation(type="refines", target="/concepts/b.md",
                            confidence="high", source="wt-r1")
    body = "# D\n\n[b](/concepts/b.md) and [c](/concepts/c.md)\n" + "x" * 300
    pages = [_page("concepts/a.md", relations=[rel], body=body),
             _page("concepts/b.md"), _page("concepts/c.md")]
    g = wiki_graph.build(pages)
    untyped = [(e["src"], e["dst"]) for e in g["edges"] if not e["typed"]]
    assert untyped == [("concepts/a.md", "concepts/c.md")]


def test_body_link_to_a_non_node_page_is_not_an_edge():
    body = "# D\n\n[src](/sources/s.md)\n" + "x" * 300
    pages = [_page("concepts/a.md", body=body), _page("sources/s.md", type="Source")]
    g = wiki_graph.build(pages)
    assert g["edges"] == []
```

- [ ] **Step 6: Run to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph.py -v`
Expected: FAIL — `assert len(typed) == 1` gets 0; `build` returns no edges.

- [ ] **Step 7: Implement edges**

Add to `src/coscience/wiki_graph.py`:

```python
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
```

Then rewrite `build`'s return to use it:

```python
def build(pages: list[wiki_okf.Page]) -> dict:
    node_pages = _node_pages(pages)
    known = {p.path for p in node_pages}
    nodes = [{
        "id": p.path, "slug": p.slug, "title": p.title or p.slug,
        "type": p.type, "status": p.status, "trust": wiki_read.trust_tier(p),
        "in_degree": 0, "out_degree": 0, "orphan": True, "cluster": 0,
    } for p in node_pages]
    return {"nodes": nodes, "edges": _edges(node_pages, known)}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
git add src/coscience/wiki_graph.py tests/test_wiki_graph.py
git commit -m "feat(wiki): graph builder — nodes and typed/untyped edges"
```

---

## Task 2: Metrics — degree, orphan, cluster, and materialized `contradicts`

**Files:**
- Modify: `src/coscience/wiki_graph.py`
- Test: `tests/test_wiki_graph_metrics.py`

**Interfaces:**
- Consumes: `build()` from Task 1.
- Produces: `build()` now populates `in_degree`, `out_degree`, `orphan`, `cluster`, and appends materialized reverse `contradicts` edges with `materialized: True`.

**Why this task is separate:** design §2 requires materialized edges be excluded from every metric. Node size is degree, so counting them would make one disagreement look like two connections — the picture would state something false. That exclusion is the defect the tests below must catch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_graph_metrics.py
from coscience import wiki_graph, wiki_okf


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 300 + "\n")
    return wiki_okf.Page(path=path, **kw)


def _rel(type_, target, source="wt-r1"):
    return wiki_okf.Relation(type=type_, target=target, confidence="high", source=source)


def _by_id(g):
    return {n["id"]: n for n in g["nodes"]}


def test_degree_counts_typed_and_untyped_edges():
    pages = [_page("concepts/a.md", relations=[_rel("refines", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    n = _by_id(wiki_graph.build(pages))
    assert (n["concepts/a.md"]["out_degree"], n["concepts/a.md"]["in_degree"]) == (1, 0)
    assert (n["concepts/b.md"]["out_degree"], n["concepts/b.md"]["in_degree"]) == (0, 1)


def test_contradicts_is_materialized_in_reverse():
    pages = [_page("concepts/a.md", relations=[_rel("contradicts", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    g = wiki_graph.build(pages)
    rev = [e for e in g["edges"] if e["materialized"]]
    assert len(rev) == 1
    assert (rev[0]["src"], rev[0]["dst"], rev[0]["type"]) == \
           ("concepts/b.md", "concepts/a.md", "contradicts")


def test_materialized_edges_do_not_count_toward_degree():
    """The defect this catches: counting the materialized reverse edge, which
    inflates BOTH ends of a single disagreement. Node size is degree, so that
    would draw a lie."""
    pages = [_page("concepts/a.md", relations=[_rel("contradicts", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    n = _by_id(wiki_graph.build(pages))
    assert (n["concepts/a.md"]["out_degree"], n["concepts/a.md"]["in_degree"]) == (1, 0)
    assert (n["concepts/b.md"]["out_degree"], n["concepts/b.md"]["in_degree"]) == (0, 1)


def test_orphan_is_degree_zero_only():
    pages = [_page("concepts/a.md", relations=[_rel("refines", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md"), _page("concepts/lonely.md")]
    n = _by_id(wiki_graph.build(pages))
    assert n["concepts/lonely.md"]["orphan"] is True
    assert n["concepts/a.md"]["orphan"] is False
    assert n["concepts/b.md"]["orphan"] is False


def test_cluster_ids_group_connected_components_ignoring_direction():
    pages = [_page("concepts/a.md", relations=[_rel("refines", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md"),
             _page("concepts/x.md", relations=[_rel("refines", "/concepts/y.md")],
                   body="# D\n\n[y](/concepts/y.md)\n" + "x" * 300),
             _page("concepts/y.md")]
    n = _by_id(wiki_graph.build(pages))
    assert n["concepts/a.md"]["cluster"] == n["concepts/b.md"]["cluster"]
    assert n["concepts/x.md"]["cluster"] == n["concepts/y.md"]["cluster"]
    assert n["concepts/a.md"]["cluster"] != n["concepts/x.md"]["cluster"]


def test_a_materialized_edge_alone_does_not_join_a_cluster_twice():
    """Cluster ids must be stable whether or not reverse edges exist."""
    pages = [_page("concepts/a.md", relations=[_rel("contradicts", "/concepts/b.md")],
                   body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300),
             _page("concepts/b.md")]
    n = _by_id(wiki_graph.build(pages))
    assert n["concepts/a.md"]["cluster"] == n["concepts/b.md"]["cluster"]
    assert sorted({v["cluster"] for v in n.values()}) == [0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph_metrics.py -v`
Expected: FAIL — degrees are all 0, `orphan` is always True, no materialized edges.

- [ ] **Step 3: Implement metrics and materialization**

Add to `src/coscience/wiki_graph.py`:

```python
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
```

Update `build`'s last lines:

```python
    edges = _edges(node_pages, known)
    edges += _materialized(edges)
    _apply_metrics(nodes, edges)
    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph.py tests/test_wiki_graph_metrics.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Prove the exclusion test can catch its defect**

Temporarily change `_apply_metrics` to `real = edges` (counting materialized edges). Run:

`~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph_metrics.py::test_materialized_edges_do_not_count_toward_degree -v`

Expected: **FAIL** — `(1, 1) != (1, 0)`. Revert the change and confirm PASS again. If it still passes, the test is vacuous and must be fixed before moving on.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/wiki_graph.py tests/test_wiki_graph_metrics.py
git commit -m "feat(wiki): graph metrics — degree, orphan, cluster, materialized contradicts"
```

---

## Task 3: Cache with a content-hash key

**Files:**
- Modify: `src/coscience/wiki_graph.py`
- Test: `tests/test_wiki_graph_cache.py`

**Interfaces:**
- Consumes: `build()`, `wiki_store.iter_pages(substrate, program_id)`, `wiki_store.state_dir(substrate, program_id)`.
- Produces: `digest(pages: list[Page]) -> str`; `cached_build(substrate, program_id) -> dict` reading/writing `.wiki/graph.json`.

**Deviation, deliberate:** design §8.1 replaces the parent spec's `(path, mtime, size)` cache key with a **content hash**. `mtime` changes on every `git checkout` while content does not, and content can change while size does not — the second case serves a stale graph. This needs a `docs/knowledge-charter.md` §7 row (Task 14).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_graph_cache.py
import json

from coscience import wiki_graph, wiki_okf, wiki_store


def _write(substrate, pid, path, body_pad="x" * 300, title="A"):
    page = wiki_okf.Page(path=path, type="Concept", title=title,
                         body="# Definition\n\n" + body_pad + "\n")
    wiki_store.write_page(substrate, pid, page)


def test_second_call_reads_the_cache(wiki_bundle):
    substrate, pid = wiki_bundle
    _write(substrate, pid, "concepts/a.md")
    first = wiki_graph.cached_build(substrate, pid)
    cache = wiki_store.state_dir(substrate, pid) / "graph.json"
    assert cache.exists()
    # Corrupt the cached payload; a cache hit must return the corrupted value,
    # proving the second call did not rebuild.
    blob = json.loads(cache.read_text())
    blob["graph"]["nodes"][0]["title"] = "FROM-CACHE"
    cache.write_text(json.dumps(blob))
    second = wiki_graph.cached_build(substrate, pid)
    assert second["nodes"][0]["title"] == "FROM-CACHE"
    assert first["nodes"][0]["title"] == "A"


def test_same_size_content_change_still_rebuilds(wiki_bundle):
    """The defect this catches: keying the cache on (mtime, size), as the parent
    spec 10 says. Both bodies below are the same byte length, so a size-based
    key serves a stale graph."""
    substrate, pid = wiki_bundle
    _write(substrate, pid, "concepts/a.md", title="AAAA")
    wiki_graph.cached_build(substrate, pid)
    _write(substrate, pid, "concepts/a.md", title="BBBB")   # identical length
    again = wiki_graph.cached_build(substrate, pid)
    assert again["nodes"][0]["title"] == "BBBB"


def test_a_corrupt_cache_file_rebuilds_instead_of_raising(wiki_bundle):
    substrate, pid = wiki_bundle
    _write(substrate, pid, "concepts/a.md")
    cache = wiki_store.state_dir(substrate, pid) / "graph.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not json")
    g = wiki_graph.cached_build(substrate, pid)
    assert [n["id"] for n in g["nodes"]] == ["concepts/a.md"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph_cache.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_graph' has no attribute 'cached_build'`

- [ ] **Step 3: Implement the cache**

Add to `src/coscience/wiki_graph.py`:

```python
import hashlib
import json


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
    except (OSError, ValueError, KeyError, TypeError):
        pass
    graph = build(pages)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "graph": graph}))
    except OSError:
        pass          # an unwritable cache must not fail the request
    return graph
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph_cache.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Prove the size-key test can catch its defect**

Temporarily replace `digest`'s body with a `(path, size)` key:

```python
    for p in sorted(pages, key=lambda x: x.path):
        h.update(f"{p.path}:{len(wiki_okf.render_page(p))}".encode())
```

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_graph_cache.py::test_same_size_content_change_still_rebuilds -v`
Expected: **FAIL** — returns `"AAAA"`. Revert and confirm PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/wiki_graph.py tests/test_wiki_graph_cache.py
git commit -m "feat(wiki): graph cache keyed on page content, not mtime and size"
```

---

## Task 4: Service method and endpoint

**Files:**
- Modify: `src/coscience/service.py`
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_wiki_graph.py`

**Interfaces:**
- Consumes: `wiki_graph.cached_build(substrate, program_id) -> dict`.
- Produces: `Service.wiki_graph(program_id) -> dict`; `GET /api/programs/{pid}/wiki/graph`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_wiki_graph.py
from coscience import wiki_okf, wiki_store


def _write(substrate, pid, path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 300 + "\n")
    wiki_store.write_page(substrate, pid, wiki_okf.Page(path=path, **kw))


def test_graph_endpoint_returns_nodes_and_edges(client, wiki_bundle):
    substrate, pid = wiki_bundle
    rel = wiki_okf.Relation(type="refines", target="/concepts/b.md",
                            confidence="high", source="wt-r1")
    _write(substrate, pid, "concepts/a.md", relations=[rel],
           body="# D\n\n[b](/concepts/b.md)\n" + "x" * 300)
    _write(substrate, pid, "concepts/b.md")
    r = client.get(f"/api/programs/{pid}/wiki/graph")
    assert r.status_code == 200
    body = r.json()
    assert sorted(n["id"] for n in body["nodes"]) == ["concepts/a.md", "concepts/b.md"]
    assert [e["type"] for e in body["edges"]] == ["refines"]


def test_graph_endpoint_404s_for_an_unknown_program(client):
    assert client.get("/api/programs/nope/wiki/graph").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_graph.py -v`
Expected: FAIL — 404 on the first test (route not registered).

> **Note:** if the `client` fixture name differs in `tests/conftest.py`, use whatever `tests/test_http_wiki_read.py` uses — match the existing file rather than inventing a fixture.

- [ ] **Step 3: Add the Service method**

In `src/coscience/service.py`, beside `wiki_lint_report`:

```python
    def wiki_graph(self, program_id: str) -> dict:
        """The concept graph for this program's bundle (spec 11.1).

        Read-only and cached; the builder is pure and lives in wiki_graph."""
        from coscience import wiki_graph
        self.substrate.load_program(program_id)      # 404s an unknown program
        return wiki_graph.cached_build(self.substrate, program_id)
```

- [ ] **Step 4: Add the endpoint**

In `src/coscience/http_api.py`, beside the other wiki GETs:

```python
    @api.get("/programs/{program_id}/wiki/graph")
    def wiki_graph(program_id: str) -> dict:
        return service.wiki_graph(program_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_graph.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the whole backend suite**

Run: `~/venvs/coscience/bin/python -m pytest -q`
Expected: exit 0, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/coscience/service.py src/coscience/http_api.py tests/test_http_wiki_graph.py
git commit -m "feat(wiki): serve the concept graph over HTTP"
```

---

## Task 5: Frontend types and API client

**Files:**
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts`

**Interfaces:**
- Consumes: `GET /api/programs/{pid}/wiki/graph` from Task 4.
- Produces: `WikiGraphNode`, `WikiGraphEdge`, `WikiGraphT` types; `api.getWikiGraph(id) -> Promise<WikiGraphT>`.

> **Naming:** the lineage feature already owns `GraphNode`/`GraphEdge`/`Graph` in this file. The wiki types MUST be `WikiGraphNode`/`WikiGraphEdge`/`WikiGraphT` — reusing the bare names would silently shadow lineage's.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/api.test.ts`, following the fetch-mocking pattern already in that file:

```ts
it("getWikiGraph fetches the program's concept graph", async () => {
  const payload = {
    nodes: [{ id: "concepts/a.md", slug: "a", title: "A", type: "Concept",
              status: "draft", trust: "unverified", in_degree: 0, out_degree: 1,
              orphan: false, cluster: 0 }],
    edges: [{ id: "t:a->b:refines", src: "concepts/a.md", dst: "concepts/b.md",
              type: "refines", confidence: "high", source: "wt-r1",
              typed: true, materialized: false }],
  };
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => payload } as Response);
  const g = await api.getWikiGraph("p1");
  expect(fetchMock).toHaveBeenCalledWith("/api/programs/p1/wiki/graph");
  expect(g.nodes[0].slug).toBe("a");
  expect(g.edges[0].typed).toBe(true);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: FAIL — `api.getWikiGraph is not a function`

- [ ] **Step 3: Add types and the client method**

In `frontend/src/api.ts`, beside the other wiki types:

```ts
export interface WikiGraphNode {
  id: string; slug: string; title: string;
  type: "Concept" | "Entity" | "Synthesis";
  status: string; trust: string;
  in_degree: number; out_degree: number; orphan: boolean; cluster: number;
}
export interface WikiGraphEdge {
  id: string; src: string; dst: string; type: string;
  confidence: string; source: string; typed: boolean; materialized: boolean;
}
export interface WikiGraphT { nodes: WikiGraphNode[]; edges: WikiGraphEdge[] }
```

and in the `api` object beside `getWikiActivity`:

```ts
  getWikiGraph: (id: string) =>
    fetch(`/api/programs/${id}/wiki/graph`).then(j<WikiGraphT>),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(wiki): api client and types for the concept graph"
```

---

## Task 6: Layout — `forceLayout` and `radialLayout`

**Files:**
- Modify: `frontend/src/components/graphLayout.ts`
- Modify: `frontend/package.json` (add `d3-force`, `@types/d3-force`)
- Test: `frontend/src/components/graphLayout.test.ts`

**Interfaces:**
- Consumes: `FlowNode`, `FlowEdge` from `./graphFlow`.
- Produces: `forceLayout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[]`; `radialLayout(centreId: string, nodes: FlowNode[]): FlowNode[]`. The existing `layout()` is UNCHANGED — `LineageGraph` depends on it.

- [ ] **Step 1: Install the dependency**

```bash
cd frontend && npm install d3-force && npm install -D @types/d3-force
```

- [ ] **Step 2: Write the failing test**

Add to `frontend/src/components/graphLayout.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { forceLayout, radialLayout } from "./graphLayout";
import type { FlowNode, FlowEdge } from "./graphFlow";

const n = (id: string): FlowNode => ({
  id, data: { label: id, stage: "", kind: "", status: "" },
  position: { x: 0, y: 0 }, style: {},
});
const e = (source: string, target: string): FlowEdge => ({
  id: `${source}->${target}`, source, target, label: "",
  data: { edge: {} as never }, animated: false, style: {},
});

describe("forceLayout", () => {
  it("is deterministic — the same graph lays out identically every time", () => {
    const nodes = ["a", "b", "c", "d"].map(n);
    const edges = [e("a", "b"), e("b", "c"), e("c", "d")];
    const first = forceLayout(nodes, edges).map((x) => x.position);
    const second = forceLayout(nodes, edges).map((x) => x.position);
    expect(second).toEqual(first);
  });

  it("separates unconnected nodes rather than stacking them at the origin", () => {
    const out = forceLayout(["a", "b", "c"].map(n), []);
    const keys = new Set(out.map((x) => `${Math.round(x.position.x)},${Math.round(x.position.y)}`));
    expect(keys.size).toBe(3);
  });
});

describe("radialLayout", () => {
  it("puts the centre at the origin and the rest on a ring around it", () => {
    const out = radialLayout("a", ["a", "b", "c", "d"].map(n));
    const at = (id: string) => out.find((x) => x.id === id)!.position;
    expect(at("a")).toEqual({ x: 0, y: 0 });
    const r = (p: { x: number; y: number }) => Math.round(Math.hypot(p.x, p.y));
    expect(r(at("b"))).toBe(r(at("c")));
    expect(r(at("b"))).toBeGreaterThan(0);
  });

  it("is deterministic", () => {
    const nodes = ["a", "b", "c"].map(n);
    expect(radialLayout("a", nodes)).toEqual(radialLayout("a", nodes));
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/graphLayout.test.ts`
Expected: FAIL — `forceLayout is not exported`

- [ ] **Step 4: Implement both layouts**

Append to `frontend/src/components/graphLayout.ts` (do not touch `layout()` above it):

```ts
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide }
  from "d3-force";

const FORCE_TICKS = 300;
const RING = 120;

/** Deterministic seed positions: d3's phyllotaxis, without its random jiggle.
 *  d3-force perturbs coincident nodes with Math.random, so an unseeded run
 *  draws a different picture every visit and the graph never becomes a shape
 *  you can learn. Seeding + a fixed tick count makes it reproducible. */
function seed(i: number): { x: number; y: number } {
  const r = 10 * Math.sqrt(0.5 + i);
  const a = i * Math.PI * (3 - Math.sqrt(5));
  return { x: r * Math.cos(a), y: r * Math.sin(a) };
}

export function forceLayout(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
  const sim = nodes.map((nd, i) => ({ id: nd.id, ...seed(i) }));
  const links = edges
    .filter((ed) => ed.source !== ed.target)
    .map((ed) => ({ source: ed.source, target: ed.target }));
  forceSimulation(sim as never[])
    .force("link", forceLink(links as never[]).id((d: never) => (d as { id: string }).id).distance(90))
    .force("charge", forceManyBody().strength(-240))
    .force("centre", forceCenter(0, 0))
    .force("collide", forceCollide(28))
    .stop()
    .tick(FORCE_TICKS);
  const at = new Map(sim.map((s) => [s.id, s]));
  return nodes.map((nd) => {
    const p = at.get(nd.id);
    return { ...nd, position: { x: p?.x ?? 0, y: p?.y ?? 0 } };
  });
}

/** A one-hop neighbourhood is under ten nodes, so no simulation is needed —
 *  and this keeps d3-force off the browse view's bundle entirely. */
export function radialLayout(centreId: string, nodes: FlowNode[]): FlowNode[] {
  const others = nodes.filter((nd) => nd.id !== centreId);
  const step = others.length ? (2 * Math.PI) / others.length : 0;
  let i = 0;
  return nodes.map((nd) => {
    if (nd.id === centreId) return { ...nd, position: { x: 0, y: 0 } };
    const a = step * i++ - Math.PI / 2;
    return { ...nd, position: { x: RING * Math.cos(a), y: RING * Math.sin(a) } };
  });
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend && npx vitest run src/components/graphLayout.test.ts`
Expected: PASS — including the pre-existing dagre `layout()` tests, which must be untouched.

- [ ] **Step 6: Prove the determinism test can catch its defect**

Temporarily change `seed` to `return { x: Math.random() * 100, y: Math.random() * 100 };`.
Run: `cd frontend && npx vitest run src/components/graphLayout.test.ts -t "deterministic"`
Expected: **FAIL** on the `forceLayout` determinism case. Revert and confirm PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/graphLayout.ts frontend/src/components/graphLayout.test.ts frontend/package.json frontend/package-lock.json
git commit -m "feat(wiki): deterministic force and radial layouts behind the graphLayout seam"
```

---

## Task 7: Encoding module

**Files:**
- Create: `frontend/src/components/wikiGraphStyle.ts`
- Test: `frontend/src/components/wikiGraphStyle.test.ts`

**Interfaces:**
- Consumes: `WikiGraphNode`, `WikiGraphEdge` from `../api`.
- Produces: `nodeStyle(n, lens)`, `edgeStyle(e, lens)`, `nodeSize(n)`, `TYPE_HUE`, `TENSION_TYPES`, and `type Lens = "structure" | "tension"`.

**Why pure and separate:** the encoding is the part with real rules (hue by type, fill by trust, size by degree) and it is far easier to test as data than through a rendered canvas.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/wikiGraphStyle.test.ts
import { describe, it, expect } from "vitest";
import { nodeStyle, edgeStyle, nodeSize, TYPE_HUE } from "./wikiGraphStyle";
import type { WikiGraphNode, WikiGraphEdge } from "../api";

const node = (over: Partial<WikiGraphNode> = {}): WikiGraphNode => ({
  id: "concepts/a.md", slug: "a", title: "A", type: "Concept",
  status: "draft", trust: "unverified",
  in_degree: 0, out_degree: 0, orphan: true, cluster: 0, ...over,
});
const edge = (over: Partial<WikiGraphEdge> = {}): WikiGraphEdge => ({
  id: "e", src: "a", dst: "b", type: "refines", confidence: "high",
  source: "wt-r1", typed: true, materialized: false, ...over,
});

describe("node encoding", () => {
  it("uses hue for page type", () => {
    expect(nodeStyle(node({ type: "Concept" }), "structure").borderColor)
      .toBe(TYPE_HUE.Concept);
    expect(nodeStyle(node({ type: "Entity" }), "structure").borderColor)
      .toBe(TYPE_HUE.Entity);
    expect(TYPE_HUE.Concept).not.toBe(TYPE_HUE.Entity);
  });

  it("uses fill for trust, hollow through solid", () => {
    const hollow = nodeStyle(node({ trust: "unverified" }), "structure").background;
    const tinted = nodeStyle(node({ trust: "machine-confirmed" }), "structure").background;
    const solid = nodeStyle(node({ trust: "human-reviewed" }), "structure").background;
    expect(new Set([hollow, tinted, solid]).size).toBe(3);
    expect(hollow).toBe("transparent");
  });

  it("rings an orphan and only an orphan", () => {
    expect(nodeStyle(node({ orphan: true }), "structure").outline).toBeTruthy();
    expect(nodeStyle(node({ orphan: false }), "structure").outline).toBeFalsy();
  });

  it("dims a deprecated page", () => {
    const d = nodeStyle(node({ status: "deprecated" }), "structure");
    expect(Number(d.opacity)).toBeLessThan(1);
    expect(d.textDecoration).toBe("line-through");
  });

  it("sizes by degree, so a better-connected node is larger", () => {
    expect(nodeSize(node({ in_degree: 4, out_degree: 3 })))
      .toBeGreaterThan(nodeSize(node({ in_degree: 0, out_degree: 1 })));
  });
});

describe("edge encoding", () => {
  it("draws untyped links fainter than typed ones", () => {
    const typed = edgeStyle(edge({ typed: true }), "structure");
    const untyped = edgeStyle(edge({ typed: false, type: "" }), "structure");
    expect(untyped.strokeDasharray).toBeTruthy();
    expect(Number(untyped.opacity)).toBeLessThan(Number(typed.opacity));
  });

  it("tension lens makes contradicts loud and everything else quiet", () => {
    const contra = edgeStyle(edge({ type: "contradicts" }), "tension");
    const partOf = edgeStyle(edge({ type: "part_of" }), "tension");
    expect(Number(contra.strokeWidth)).toBeGreaterThan(Number(partOf.strokeWidth));
    expect(Number(partOf.opacity)).toBeLessThan(Number(contra.opacity));
  });

  it("structure lens does not single out contradicts", () => {
    const contra = edgeStyle(edge({ type: "contradicts" }), "structure");
    const partOf = edgeStyle(edge({ type: "part_of" }), "structure");
    expect(contra.strokeWidth).toBe(partOf.strokeWidth);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/wikiGraphStyle.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the encoding**

```ts
// frontend/src/components/wikiGraphStyle.ts
// Encoding for the wiki concept graph (design 5.1).
//
// Hue carries page type, fill carries trust. The parent spec 10 says trust is
// for colouring and 11.4 says colour is by type; splitting the channels
// satisfies both. Fill deliberately mirrors .wiki-dot--* in the browse view so
// a marker means the same thing in both places.
import type { WikiGraphNode, WikiGraphEdge } from "../api";

export type Lens = "structure" | "tension";

export const TYPE_HUE: Record<string, string> = {
  Concept: "#3b82f6",     // blue
  Entity: "#a855f7",      // purple
  Synthesis: "#16a34a",   // green
};

const TRUST_FILL: Record<string, string> = {
  "unverified": "transparent",
  "machine-confirmed": "#e9ecef",
  "human-reviewed": "#c9d8ee",
};

export const TENSION_TYPES = new Set(["contradicts", "replaces", "refines"]);

const TENSION_COLOUR: Record<string, string> = {
  contradicts: "#dc2626",
  replaces: "#ea580c",
  refines: "#d97706",
};

export function nodeSize(n: WikiGraphNode): number {
  return 14 + Math.min(26, 4 * Math.sqrt(n.in_degree + n.out_degree));
}

export function nodeStyle(n: WikiGraphNode, _lens: Lens): Record<string, string> {
  const deprecated = n.status === "deprecated";
  return {
    borderColor: TYPE_HUE[n.type] ?? "#8a8f98",
    background: TRUST_FILL[n.trust] ?? "transparent",
    outline: n.orphan ? "2px dotted #8a8f98" : "",
    opacity: deprecated ? "0.4" : "1",
    textDecoration: deprecated ? "line-through" : "none",
  };
}

export function edgeStyle(e: WikiGraphEdge, lens: Lens): Record<string, string> {
  if (lens === "tension") {
    const loud = TENSION_TYPES.has(e.type);
    return {
      stroke: loud ? (TENSION_COLOUR[e.type] ?? "#dc2626") : "#c9ccd1",
      strokeWidth: loud ? (e.type === "contradicts" ? "3" : "2") : "1",
      strokeDasharray: e.typed ? "" : "3 3",
      opacity: loud ? "1" : "0.15",
    };
  }
  return {
    stroke: "#8a8f98",
    strokeWidth: "1",
    strokeDasharray: e.typed ? "" : "3 3",
    opacity: e.typed ? "0.75" : "0.35",
  };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npx vitest run src/components/wikiGraphStyle.test.ts`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/wikiGraphStyle.ts frontend/src/components/wikiGraphStyle.test.ts
git commit -m "feat(wiki): graph encoding — hue by type, fill by trust, size by degree"
```

---

## Task 8: `WikiGraphView` and its route

**Files:**
- Create: `frontend/src/views/WikiGraphView.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/views/WikiGraphView.test.tsx`

**Interfaces:**
- Consumes: `api.getWikiGraph`, `forceLayout`, `nodeStyle`/`edgeStyle`/`nodeSize`, `type Lens`.
- Produces: default-exported `WikiGraphView`; route `/programs/:id/wiki/graph`.

**Route ordering is the trap.** `App.tsx` sends `/programs/:id/wiki/*` to `WikiView` as a slug catch-all. `/wiki/graph` must be registered **before** it. Phase 3 shipped a route-ordering test that could not detect mis-ordering; the test below must.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/views/WikiGraphView.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { api } from "../api";

const graph = {
  nodes: [
    { id: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept",
      status: "draft", trust: "unverified", in_degree: 0, out_degree: 1,
      orphan: false, cluster: 0 },
    { id: "concepts/b.md", slug: "b", title: "Beta", type: "Concept",
      status: "draft", trust: "human-reviewed", in_degree: 1, out_degree: 0,
      orphan: false, cluster: 0 },
  ],
  edges: [
    { id: "t:a->b:refines", src: "concepts/a.md", dst: "concepts/b.md",
      type: "refines", confidence: "high", source: "wt-r1",
      typed: true, materialized: false },
  ],
};

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WikiGraphView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getWikiGraph").mockResolvedValue(graph as never);
  });

  it("routes /wiki/graph to the graph view, not to a page named 'graph'", async () => {
    // The defect this catches: registering /wiki/graph AFTER /wiki/*, which
    // makes WikiView render a wiki page whose slug is "graph".
    const spy = vi.spyOn(api, "getWikiPage");
    renderAt("/programs/p1/wiki/graph");
    await waitFor(() => expect(api.getWikiGraph).toHaveBeenCalledWith("p1"));
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders one element per node, labelled by title", async () => {
    renderAt("/programs/p1/wiki/graph");
    expect(await screen.findByTitle("Alpha")).toBeTruthy();
    expect(await screen.findByTitle("Beta")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiGraphView.test.tsx`
Expected: FAIL — `getWikiGraph` never called; `WikiView` renders instead.

- [ ] **Step 3: Create the view**

```tsx
// frontend/src/views/WikiGraphView.tsx
import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type WikiGraphNode } from "../api";
import { forceLayout } from "../components/graphLayout";
import { nodeStyle, edgeStyle, nodeSize, type Lens } from "../components/wikiGraphStyle";

export default function WikiGraphView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const lens: Lens = "structure";
  const q = useQuery({ queryKey: ["wiki-graph", id], queryFn: () => api.getWikiGraph(id) });

  const placed = useMemo(() => {
    if (!q.data) return [];
    const flow = q.data.nodes.map((n) => ({
      id: n.id, data: { label: n.title, stage: "", kind: "", status: n.status },
      position: { x: 0, y: 0 }, style: {},
    }));
    const links = q.data.edges.map((e) => ({
      id: e.id, source: e.src, target: e.dst, label: e.type,
      data: { edge: e as never }, animated: false, style: {},
    }));
    return forceLayout(flow, links);
  }, [q.data]);

  if (q.isLoading) return <div className="wiki-graph">Loading the graph…</div>;
  if (q.isError) return <div className="wiki-graph">Could not load the graph.</div>;

  const byId = new Map<string, WikiGraphNode>((q.data?.nodes ?? []).map((n) => [n.id, n]));

  return (
    <div className="wiki-graph">
      <svg role="img" aria-label="concept graph" width="100%" height="640">
        {(q.data?.edges ?? []).map((e) => {
          const a = placed.find((p) => p.id === e.src);
          const b = placed.find((p) => p.id === e.dst);
          if (!a || !b) return null;
          const s = edgeStyle(e, lens);
          return (
            <line key={e.id} x1={a.position.x + 400} y1={a.position.y + 320}
                  x2={b.position.x + 400} y2={b.position.y + 320}
                  stroke={s.stroke} strokeWidth={Number(s.strokeWidth)}
                  strokeDasharray={s.strokeDasharray || undefined}
                  opacity={Number(s.opacity)} />
          );
        })}
        {placed.map((p) => {
          const n = byId.get(p.id);
          if (!n) return null;
          const st = nodeStyle(n, lens);
          return (
            <circle key={p.id} cx={p.position.x + 400} cy={p.position.y + 320}
                    r={nodeSize(n) / 2}
                    fill={st.background === "transparent" ? "none" : st.background}
                    stroke={st.borderColor} strokeWidth={2}
                    opacity={Number(st.opacity)}
                    style={{ cursor: "pointer" }}
                    onClick={() => nav(`/programs/${id}/wiki/${n.slug}`)}>
              <title>{n.title}</title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}
```

- [ ] **Step 4: Register the route BEFORE the catch-all**

In `frontend/src/App.tsx`, add the lazy import beside the other view imports and insert the route **between** `/wiki/lint` and `/wiki/*`:

```tsx
              <Route path="/programs/:id/wiki" element={<WikiView />} />
              <Route path="/programs/:id/wiki/lint" element={<WikiLintView />} />
              <Route path="/programs/:id/wiki/graph" element={<WikiGraphView />} />
              <Route path="/programs/:id/wiki/*" element={<WikiView />} />
```

Import it lazily, following `LineageCard`'s pattern, so `d3-force` stays out of the main bundle:

```tsx
const WikiGraphView = lazy(() => import("./views/WikiGraphView"));
```

If `App.tsx` does not already wrap its `<Routes>` in a `<Suspense>`, wrap the graph route's element in one: `<Suspense fallback={<div>Loading…</div>}><WikiGraphView /></Suspense>`.

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiGraphView.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 6: Prove the route-ordering test can catch its defect**

Move the `/wiki/graph` route to **after** `/wiki/*`. Run the same command.
Expected: **FAIL** — `getWikiGraph` not called and `getWikiPage` was. Restore the order and confirm PASS.

- [ ] **Step 7: Add a link from the browse view**

In `frontend/src/views/WikiView.tsx`, beside the existing `wiki/lint` link (~line 156):

```tsx
<Link to={`/programs/${id}/wiki/graph`} className="view" style={{ fontSize: 13 }}>
  Graph
</Link>
```

- [ ] **Step 8: Run the frontend suite and typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all pass, `tsc` exit 0.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/views/WikiGraphView.tsx frontend/src/views/WikiGraphView.test.tsx frontend/src/App.tsx frontend/src/views/WikiView.tsx
git commit -m "feat(wiki): the concept graph view and its route"
```

---

## Task 9: Filters, typed-only toggle, and the visible count

**Files:**
- Modify: `frontend/src/views/WikiGraphView.tsx`
- Create: `frontend/src/components/wikiGraphFilter.ts`
- Test: `frontend/src/components/wikiGraphFilter.test.ts`, `frontend/src/views/WikiGraphView.test.tsx`

**Interfaces:**
- Consumes: `WikiGraphT`.
- Produces: `applyFilters(graph, f: Filters) -> WikiGraphT` and `interface Filters { types: Set<string>; relations: Set<string>; trust: Set<string>; typedOnly: boolean }`.

**Two semantics are non-negotiable (design §5.3):** filtering hides but never re-layouts, and filtering never manufactures orphans. Positions come from the *unfiltered* graph; `orphan` is server-computed and must never be recomputed client-side.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/wikiGraphFilter.test.ts
import { describe, it, expect } from "vitest";
import { applyFilters, emptyFilters } from "./wikiGraphFilter";
import type { WikiGraphT } from "../api";

const g: WikiGraphT = {
  nodes: [
    { id: "a", slug: "a", title: "A", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 0, out_degree: 1, orphan: false, cluster: 0 },
    { id: "b", slug: "b", title: "B", type: "Entity", status: "draft",
      trust: "human-reviewed", in_degree: 1, out_degree: 0, orphan: false, cluster: 0 },
  ],
  edges: [
    { id: "e1", src: "a", dst: "b", type: "refines", confidence: "high",
      source: "s", typed: true, materialized: false },
    { id: "e2", src: "a", dst: "b", type: "", confidence: "", source: "",
      typed: false, materialized: false },
  ],
};

it("typedOnly drops untyped body-link edges", () => {
  const out = applyFilters(g, { ...emptyFilters(), typedOnly: true });
  expect(out.edges.map((e) => e.id)).toEqual(["e1"]);
});

it("filtering by node type hides the node and its edges", () => {
  const out = applyFilters(g, { ...emptyFilters(), types: new Set(["Concept"]) });
  expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
  expect(out.edges).toEqual([]);
});

it("never manufactures an orphan", () => {
  // The defect this catches: recomputing `orphan` from the FILTERED edge set,
  // which rings a node whose edges the reader merely hid. `orphan` is a
  // whole-graph property computed server-side.
  const out = applyFilters(g, { ...emptyFilters(), relations: new Set(["is_a"]) });
  expect(out.edges).toEqual([]);
  expect(out.nodes.every((n) => n.orphan === false)).toBe(true);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/wikiGraphFilter.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the filter**

```ts
// frontend/src/components/wikiGraphFilter.ts
import type { WikiGraphT } from "../api";

export interface Filters {
  types: Set<string>;       // empty = all
  relations: Set<string>;   // empty = all
  trust: Set<string>;       // empty = all
  typedOnly: boolean;
}

export function emptyFilters(): Filters {
  return { types: new Set(), relations: new Set(), trust: new Set(), typedOnly: false };
}

/** Hides; never re-layouts and never recomputes `orphan`.
 *  `orphan` is a whole-graph property from the server — deriving it from the
 *  filtered edge set would ring nodes the reader merely hid, reporting a data
 *  problem that does not exist (design 5.3). Node objects pass through
 *  untouched for exactly that reason. */
export function applyFilters(g: WikiGraphT, f: Filters): WikiGraphT {
  const keep = (s: Set<string>, v: string) => s.size === 0 || s.has(v);
  const nodes = g.nodes.filter((n) => keep(f.types, n.type) && keep(f.trust, n.trust));
  const live = new Set(nodes.map((n) => n.id));
  const edges = g.edges.filter((e) =>
    live.has(e.src) && live.has(e.dst)
    && (!f.typedOnly || e.typed)
    && (e.typed ? keep(f.relations, e.type) : f.relations.size === 0));
  return { nodes, edges };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npx vitest run src/components/wikiGraphFilter.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Prove the orphan test can catch its defect**

Temporarily add, before the `return` in `applyFilters`:

```ts
  const deg = new Map<string, number>();
  for (const e of edges) { deg.set(e.src, (deg.get(e.src) ?? 0) + 1); deg.set(e.dst, (deg.get(e.dst) ?? 0) + 1); }
  for (const n of nodes) n.orphan = !deg.get(n.id);
```

Run: `cd frontend && npx vitest run src/components/wikiGraphFilter.test.ts -t "manufactures"`
Expected: **FAIL**. Remove the block and confirm PASS.

- [ ] **Step 6: Wire the controls into the view**

In `WikiGraphView.tsx`: hold `Filters` in state, compute `const shown = applyFilters(q.data, filters)`, and render from `shown` — but keep `placed` computed from the **unfiltered** `q.data`, so positions never move. Render checkbox groups for type, relation type and trust, a `typed only` checkbox, and a count:

```tsx
<p className="eyebrow">
  showing {shown.nodes.length} of {q.data.nodes.length} nodes
</p>
```

- [ ] **Step 7: Add the view test**

Append to `frontend/src/views/WikiGraphView.test.tsx`:

```tsx
it("reports how many nodes are visible, and filtering does not move the rest", async () => {
  renderAt("/programs/p1/wiki/graph");
  const before = await screen.findByText(/showing 2 of 2 nodes/);
  expect(before).toBeTruthy();
  const alpha = await screen.findByTitle("Alpha");
  const cx = alpha.parentElement?.getAttribute("cx");
  fireEvent.click(screen.getByLabelText("typed only"));
  await waitFor(() => expect(screen.getByText(/showing 2 of 2 nodes/)).toBeTruthy());
  expect(alpha.parentElement?.getAttribute("cx")).toBe(cx);   // layout unmoved
});
```

Add `fireEvent` to the `@testing-library/react` import. **Do not use `user-event`** — it is not a dependency.

- [ ] **Step 8: Run the frontend suite**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/wikiGraphFilter.ts frontend/src/components/wikiGraphFilter.test.ts frontend/src/views/WikiGraphView.tsx frontend/src/views/WikiGraphView.test.tsx
git commit -m "feat(wiki): graph filters, typed-only toggle and visible-count readout"
```

---

## Task 10: The tension lens

**Files:**
- Modify: `frontend/src/views/WikiGraphView.tsx`
- Test: `frontend/src/views/WikiGraphView.test.tsx`

**Interfaces:**
- Consumes: `edgeStyle(e, lens)` and `type Lens` from Task 7 — already lens-aware, so this task only adds the control and proves layout stability.

- [ ] **Step 1: Write the failing test**

```tsx
it("the tension lens restyles edges without moving anything", async () => {
  // The defect this catches: recomputing layout when the lens changes. A
  // toggle that rearranges the picture is one the reader stops trusting.
  renderAt("/programs/p1/wiki/graph");
  const alpha = await screen.findByTitle("Alpha");
  const before = alpha.parentElement?.getAttribute("cx");
  fireEvent.click(screen.getByLabelText("tension"));
  await waitFor(() => expect(screen.getByLabelText("tension")).toBeChecked());
  expect(alpha.parentElement?.getAttribute("cx")).toBe(before);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiGraphView.test.tsx -t "tension"`
Expected: FAIL — no control labelled "tension".

- [ ] **Step 3: Add the lens control**

Replace `const lens: Lens = "structure";` with state, and add the control:

```tsx
const [lens, setLens] = useState<Lens>("structure");
```

```tsx
<label>
  <input type="checkbox" aria-label="tension"
         checked={lens === "tension"}
         onChange={(e) => setLens(e.target.checked ? "tension" : "structure")} />
  tension
</label>
```

`placed` must **not** list `lens` in its `useMemo` dependency array — that is precisely what would move the nodes.

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiGraphView.test.tsx`
Expected: PASS

- [ ] **Step 5: Prove the test can catch its defect**

Add `lens` to `placed`'s dependency array and change the seed so a re-run differs (e.g. `forceLayout(flow, lens === "tension" ? [] : links)`).
Run the same test. Expected: **FAIL**. Revert both changes and confirm PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/WikiGraphView.tsx frontend/src/views/WikiGraphView.test.tsx
git commit -m "feat(wiki): tension lens — restyles edges, never moves the layout"
```

---

## Task 11: Focus mode and the neighbourhood pane

**Files:**
- Create: `frontend/src/components/wikiNeighbourhood.ts` (pure), `frontend/src/components/WikiNeighbourhood.tsx`
- Modify: `frontend/src/views/WikiGraphView.tsx`, `frontend/src/views/WikiView.tsx`
- Test: `frontend/src/components/wikiNeighbourhood.test.ts`, `frontend/src/components/WikiNeighbourhood.test.tsx`

**Interfaces:**
- Consumes: `WikiGraphT`, `radialLayout` from Task 6.
- Produces: `neighbourhood(g: WikiGraphT, centreId: string, hops: number) -> WikiGraphT`; `<WikiNeighbourhood programId slug />`.

Focus mode and the pane are the same reduction, so they share `neighbourhood()`. The pane uses `radialLayout`, **never** `forceLayout`, keeping `d3-force` off the browse view's bundle (design §6).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/wikiNeighbourhood.test.ts
import { describe, it, expect } from "vitest";
import { neighbourhood } from "./wikiNeighbourhood";
import type { WikiGraphT } from "../api";

const n = (id: string) => ({
  id, slug: id, title: id.toUpperCase(), type: "Concept" as const,
  status: "draft", trust: "unverified", in_degree: 1, out_degree: 1,
  orphan: false, cluster: 0,
});
const e = (src: string, dst: string) => ({
  id: `${src}->${dst}`, src, dst, type: "refines", confidence: "high",
  source: "s", typed: true, materialized: false,
});
const g: WikiGraphT = { nodes: ["a", "b", "c", "d"].map(n),
                        edges: [e("a", "b"), e("b", "c"), e("c", "d")] };

it("one hop is the centre and its immediate neighbours", () => {
  const out = neighbourhood(g, "b", 1);
  expect(out.nodes.map((x) => x.id).sort()).toEqual(["a", "b", "c"]);
});

it("two hops reaches one step further", () => {
  const out = neighbourhood(g, "b", 2);
  expect(out.nodes.map((x) => x.id).sort()).toEqual(["a", "b", "c", "d"]);
});

it("direction is ignored — an inbound neighbour counts", () => {
  expect(neighbourhood(g, "b", 1).nodes.map((x) => x.id)).toContain("a");
});

it("an unknown centre yields an empty graph rather than throwing", () => {
  expect(neighbourhood(g, "sources/x.md", 1)).toEqual({ nodes: [], edges: [] });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/wikiNeighbourhood.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the reduction**

```ts
// frontend/src/components/wikiNeighbourhood.ts
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npx vitest run src/components/wikiNeighbourhood.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the pane's failing test**

```tsx
// frontend/src/components/WikiNeighbourhood.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WikiNeighbourhood from "./WikiNeighbourhood";
import { api } from "../api";

const graph = {
  nodes: [
    { id: "concepts/a.md", slug: "a", title: "Alpha", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 0, out_degree: 1, orphan: false, cluster: 0 },
    { id: "concepts/b.md", slug: "b", title: "Beta", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 1, out_degree: 0, orphan: false, cluster: 0 },
  ],
  edges: [{ id: "e", src: "concepts/a.md", dst: "concepts/b.md", type: "refines",
            confidence: "high", source: "s", typed: true, materialized: false }],
};

function show(slug: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WikiNeighbourhood programId="p1" slug={slug} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getWikiGraph").mockResolvedValue(graph as never);
});

it("shows the current page and its neighbours", async () => {
  show("a");
  expect(await screen.findByTitle("Alpha")).toBeTruthy();
  expect(await screen.findByTitle("Beta")).toBeTruthy();
});

it("tells the reader when the page is not in the graph at all", async () => {
  // Source pages are excluded from the graph by design; an empty box reads as
  // broken, so the pane must say so instead.
  show("result-wt-r1");
  expect(await screen.findByText(/not in the concept graph/i)).toBeTruthy();
});
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/WikiNeighbourhood.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 7: Implement the pane**

```tsx
// frontend/src/components/WikiNeighbourhood.tsx
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { neighbourhood } from "./wikiNeighbourhood";
import { radialLayout } from "./graphLayout";
import { nodeStyle, nodeSize } from "./wikiGraphStyle";

const BOX = 240, HALF = BOX / 2;

export default function WikiNeighbourhood(
  { programId, slug }: { programId: string; slug: string },
) {
  const q = useQuery({ queryKey: ["wiki-graph", programId],
                       queryFn: () => api.getWikiGraph(programId) });
  const centreId = useMemo(
    () => q.data?.nodes.find((n) => n.slug === slug)?.id ?? "",
    [q.data, slug]);
  const sub = useMemo(
    () => (q.data && centreId ? neighbourhood(q.data, centreId, 1) : { nodes: [], edges: [] }),
    [q.data, centreId]);
  const placed = useMemo(
    () => radialLayout(centreId, sub.nodes.map((n) => ({
      id: n.id, data: { label: n.title, stage: "", kind: "", status: n.status },
      position: { x: 0, y: 0 }, style: {},
    }))),
    [sub, centreId]);

  if (q.isLoading) return null;
  if (!centreId) {
    return (
      <p className="muted" style={{ fontSize: 12 }}>
        This page is not in the concept graph — source pages are excluded.
      </p>
    );
  }

  const byId = new Map(sub.nodes.map((n) => [n.id, n]));
  return (
    <div>
      <svg role="img" aria-label="neighbourhood" width={BOX} height={BOX}>
        {sub.edges.map((e) => {
          const a = placed.find((p) => p.id === e.src);
          const b = placed.find((p) => p.id === e.dst);
          if (!a || !b) return null;
          return <line key={e.id} x1={a.position.x + HALF} y1={a.position.y + HALF}
                       x2={b.position.x + HALF} y2={b.position.y + HALF}
                       stroke="#8a8f98" strokeWidth={1} />;
        })}
        {placed.map((p) => {
          const n = byId.get(p.id);
          if (!n) return null;
          const st = nodeStyle(n, "structure");
          return (
            <circle key={p.id} cx={p.position.x + HALF} cy={p.position.y + HALF}
                    r={nodeSize(n) / 2}
                    fill={st.background === "transparent" ? "none" : st.background}
                    stroke={st.borderColor}
                    strokeWidth={p.id === centreId ? 3 : 2}>
              <title>{n.title}</title>
            </circle>
          );
        })}
      </svg>
      <Link to={`/programs/${programId}/wiki/graph?focus=${slug}`}
            className="view" style={{ fontSize: 12 }}>
        open full graph ↗
      </Link>
    </div>
  );
}
```

- [ ] **Step 8: Mount it in `WikiView`**

In `frontend/src/views/WikiView.tsx`, inside the `.wiki-side` aside (near the existing `trust` block, ~line 383), render it only when a slug is selected:

```tsx
{slug && (
  <>
    <div className="eyebrow" style={{ marginBottom: 8 }}>neighbourhood</div>
    <WikiNeighbourhood programId={id} slug={slug} />
  </>
)}
```

Import it normally (not lazily) — it pulls no heavy dependency, which is the point of `radialLayout`.

- [ ] **Step 9: Add focus mode to the full view**

In `WikiGraphView.tsx`, read the query parameter and reduce before laying out:

Add `useSearchParams` to the existing `react-router-dom` import first.

```tsx
const [params] = useSearchParams();
const focus = params.get("focus") ?? "";
```

Then, where `q.data` feeds the layout, substitute the reduced graph when `focus` names a node:

```tsx
const source = useMemo(() => {
  if (!q.data) return undefined;
  const id = q.data.nodes.find((n) => n.slug === focus)?.id;
  return id ? neighbourhood(q.data, id, 2) : q.data;
}, [q.data, focus]);
```

Use `source` in place of `q.data` for `placed`, `shown` and the count. Add a "show whole graph" link clearing `?focus=` when it is set.

- [ ] **Step 10: Run the frontend suite and typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/components/wikiNeighbourhood.ts frontend/src/components/wikiNeighbourhood.test.ts frontend/src/components/WikiNeighbourhood.tsx frontend/src/components/WikiNeighbourhood.test.tsx frontend/src/views/WikiView.tsx frontend/src/views/WikiGraphView.tsx
git commit -m "feat(wiki): neighbourhood pane and graph focus mode"
```

---

## Task 12: Provenance backlinks

**Files:**
- Modify: `src/coscience/wiki_read.py`, `src/coscience/service.py`, `src/coscience/http_api.py`
- Modify: `frontend/src/api.ts`, `frontend/src/views/SprintDetail.tsx`, `frontend/src/views/ArtifactDetail.tsx`
- Test: `tests/test_wiki_citations.py`, `frontend/src/views/ArtifactDetail.test.tsx`

**Interfaces:**
- Consumes: `wiki_store.iter_pages`, `wiki_okf.Page.sources`.
- Produces: `wiki_read.citing_pages(pages, oid) -> list[dict]` with keys `path`, `slug`, `title`, `type`; `Service.wiki_citations(program_id, oid) -> list[dict]`; `GET /api/programs/{pid}/wiki/citations/{oid}`; `api.getWikiCitations(id, oid)`.

The chain: an object id (`result:wt-r1`) matches a Source page's `origin`; pages citing that Source page name it in `sources[].resource`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_citations.py
from coscience import wiki_okf, wiki_read


def _src(path, origin):
    return wiki_okf.Page(path=path, type="Source", title="S",
                         body="# Summary\n\n" + "x" * 300,
                         extra={"origin": origin})


def _concept(path, resource):
    return wiki_okf.Page(
        path=path, type="Concept", title=path.rsplit("/", 1)[-1][:-3],
        body="# Definition\n\n" + "x" * 300,
        sources=[wiki_okf.Source(id="wt-r1", resource=resource, title="S")])


def test_finds_pages_citing_an_object():
    pages = [_src("sources/result-wt-r1.md", "result:wt-r1"),
             _concept("concepts/a.md", "/sources/result-wt-r1.md"),
             _concept("concepts/b.md", "/sources/result-other.md")]
    out = wiki_read.citing_pages(pages, "result:wt-r1")
    assert [c["path"] for c in out] == ["concepts/a.md"]
    assert out[0]["slug"] == "a"


def test_unknown_object_yields_nothing_rather_than_raising():
    assert wiki_read.citing_pages([], "result:nope") == []


def test_a_source_page_does_not_cite_itself():
    pages = [_src("sources/result-wt-r1.md", "result:wt-r1")]
    assert wiki_read.citing_pages(pages, "result:wt-r1") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_citations.py -v`
Expected: FAIL — `wiki_read` has no attribute `citing_pages`.

- [ ] **Step 3: Implement the lookup**

Add to `src/coscience/wiki_read.py`:

```python
def citing_pages(pages: list[wiki_okf.Page], oid: str) -> list[dict]:
    """Pages citing the object `oid`, the reverse of the `cited from` chips.

    object id -> the Source page whose `origin` is that id -> every page naming
    that Source page in `sources[].resource`."""
    if not oid:
        return []
    source_paths = {p.path for p in pages
                    if p.type == "Source" and str(p.extra.get("origin", "")) == oid}
    if not source_paths:
        return []
    out = []
    for p in pages:
        if p.type == "Source":
            continue
        refs = {str(s.resource or "").lstrip("/") for s in p.sources}
        if refs & source_paths:
            out.append({"path": p.path, "slug": p.slug,
                        "title": p.title or p.slug, "type": p.type})
    return sorted(out, key=lambda c: c["path"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_citations.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add Service method and endpoint**

In `service.py`:

```python
    def wiki_citations(self, program_id: str, oid: str) -> list[dict]:
        """Which wiki pages cite this result or artifact version (design 7)."""
        from coscience import wiki_read, wiki_store
        self.substrate.load_program(program_id)
        return wiki_read.citing_pages(
            wiki_store.iter_pages(self.substrate, program_id), oid)
```

In `http_api.py`, beside the other wiki GETs:

```python
    @api.get("/programs/{program_id}/wiki/citations/{oid:path}")
    def wiki_citations(program_id: str, oid: str) -> list[dict]:
        return service.wiki_citations(program_id, oid)
```

`{oid:path}` is required — an artifact oid contains `@` and an id may contain `/`.

- [ ] **Step 6: Add the HTTP test**

Append to `tests/test_wiki_citations.py`, using the same `client`/`wiki_bundle` fixtures as `tests/test_http_wiki_graph.py`:

```python
def test_citations_endpoint(client, wiki_bundle):
    from coscience import wiki_store
    substrate, pid = wiki_bundle
    wiki_store.write_page(substrate, pid, _src("sources/result-wt-r1.md", "result:wt-r1"))
    wiki_store.write_page(substrate, pid, _concept("concepts/a.md", "/sources/result-wt-r1.md"))
    r = client.get(f"/api/programs/{pid}/wiki/citations/result:wt-r1")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()] == ["a"]
```

- [ ] **Step 7: Add the frontend client**

In `frontend/src/api.ts`:

```ts
export interface WikiCitation { path: string; slug: string; title: string; type: string }
```

```ts
  getWikiCitations: (id: string, oid: string) =>
    fetch(`/api/programs/${id}/wiki/citations/${encodeURIComponent(oid)}`)
      .then(j<WikiCitation[]>),
```

- [ ] **Step 8: Render chips on both detail views**

In `SprintDetail.tsx`, for each result id, query `getWikiCitations(programId, \`result:${resultId}\`)` and render the returned titles as links to `/programs/:id/wiki/:slug`.

In `ArtifactDetail.tsx`, query with `` `artifact:${aid}@${versionId}` ``. When the shown version is **not** the artifact's `current`, render this instead of an empty row:

```tsx
<p className="muted" style={{ fontSize: 12 }}>
  Not ingested — the wiki tracks only the current version.
</p>
```

- [ ] **Step 9: Finish the neighbourhood pane's Source-page case**

Design 6 says a Source page's pane lists the pages citing it. Task 11 left it as
a bare message because `getWikiCitations` did not exist yet; it does now. In
`WikiNeighbourhood.tsx`, when `centreId` is empty, query citations for this
page's object id and list them:

```tsx
const cites = useQuery({
  queryKey: ["wiki-citations", programId, slug],
  enabled: !centreId,
  queryFn: () => api.getWikiCitations(programId, oidForSourceSlug(slug)),
});
```

where `oidForSourceSlug` inverts `wiki_store`'s slug rule — `sources/result-<id>.md`
came from `result:<id>`, and `sources/artifact-<aid>-<vid>.md` from
`artifact:<aid>@<vid>`. Put that helper in `wikiNeighbourhood.ts` with its own
unit test covering both shapes, since it is pure string logic and easy to get
wrong. Render the returned titles as links; keep the existing
"not in the concept graph" line above them.

- [ ] **Step 10: Add the ArtifactDetail test**

```tsx
it("explains an old version has no citations rather than showing an empty row", async () => {
  // program_objects ingests the CURRENT version only, so a non-current version
  // always has zero backlinks. Rendering nothing reads as a bug.
  vi.spyOn(api, "getWikiCitations").mockResolvedValue([]);
  // ...render ArtifactDetail on a NON-current version, per this file's existing pattern...
  expect(await screen.findByText(/only the current version/i)).toBeTruthy();
});
```

- [ ] **Step 11: Run both suites**

Run: `~/venvs/coscience/bin/python -m pytest -q && cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add src/coscience/wiki_read.py src/coscience/service.py src/coscience/http_api.py tests/test_wiki_citations.py frontend/src/api.ts frontend/src/views/SprintDetail.tsx frontend/src/views/ArtifactDetail.tsx frontend/src/views/ArtifactDetail.test.tsx frontend/src/components/WikiNeighbourhood.tsx frontend/src/components/wikiNeighbourhood.ts frontend/src/components/wikiNeighbourhood.test.ts
git commit -m "feat(wiki): provenance backlinks from results and artifacts to citing pages"
```

---

## Task 13: Human-accepted merges in the Activity list

**Files:**
- Modify: `src/coscience/service.py` (`accept_wiki_merge`, ~line 1826)
- Test: `tests/test_service_wiki_merge.py`

**Interfaces:**
- Consumes: `state["runs"]`, `wiki.RUNS_KEPT`.
- Produces: an entry with `by: "human"` appended by `accept_wiki_merge`.

**Why here:** ruled by the human on 2026-08-28 (design §10). It amends the parent spec §11.3, which defines Activity as what *runs* did. The entry must distinguish a human action from a run, or the view implies an agent did it. This lands before any further UI is built on `state["runs"]`.

- [ ] **Step 1: Write the failing test**

```python
def test_accepting_a_merge_records_it_in_the_activity_trail(wiki_bundle):
    """Ruled 2026-08-28: Activity shows everything that changed the wiki, not
    only what runs did. The entry must say a human did it."""
    substrate, pid = wiki_bundle
    # ...arrange a queued proposal exactly as the neighbouring accept tests do...
    svc.accept_wiki_merge(pid, merge_id)
    state = wiki_store.load_state(substrate, pid)
    entry = state["runs"][0]
    assert entry["kind"] == "merge"
    assert entry["by"] == "human"
    assert entry["merged"] and entry["merged"][0]["winner"] == winner
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_merge.py -k activity -v`
Expected: FAIL — `state["runs"]` is empty; `accept_wiki_merge` never appends.

- [ ] **Step 3: Append the entry**

In `accept_wiki_merge`, after `merge_wiki_pages` succeeds and inside a `state_guard` block:

```python
        from coscience.wiki import RUNS_KEPT
        with wiki_store.state_guard(self.substrate, program_id) as state:
            state["runs"] = ([{
                "id": merge_id, "kind": "merge", "by": "human",
                "status": "ok", "at": time.time(),
                "pages_created": 0, "pages_updated": len(out.get("rewritten") or []),
                "merged": [{"loser": loser, "winner": winner,
                            "commit": out.get("commit", "")}],
            }] + list(state.get("runs") or []))[:RUNS_KEPT]
```

- [ ] **Step 4: Run to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_merge.py -v`
Expected: PASS

- [ ] **Step 5: Show it in the view**

In `WikiLintView.tsx`'s activity list, render a "human" marker when `by === "human"`, so a reader cannot mistake it for an agent's work. Add a scoped assertion to `WikiLintView.test.tsx`.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/service.py tests/test_service_wiki_merge.py frontend/src/views/WikiLintView.tsx frontend/src/views/WikiLintView.test.tsx
git commit -m "feat(wiki): record human-accepted merges in the activity trail"
```

---

## Task 14: Record the deviations and close the phase

**Files:**
- Modify: `docs/knowledge-charter.md` (§7 decision log, §6 seams)
- Modify: `docs/knowledge/NEXT.md`

- [ ] **Step 1: Add three decision-log rows**

In `docs/knowledge-charter.md` §7, one row each, with the reason:

1. Graph cache keyed on page **content**, not `(path, mtime, size)` — mtime churns on checkout; equal-size content changes serve a stale graph. Amends parent §10.
2. Hue carries page type, fill carries trust — parent §10 and §11.4 each claim colour; splitting the channels satisfies both.
3. Materialized `contradicts` edges excluded from degree, orphan and cluster — node size is degree, so counting them would draw one disagreement as two connections.

- [ ] **Step 2: Add the new seams to §6**

`wiki_graph.py` (pure builder), `.wiki/graph.json` (content-keyed cache), `graphLayout.ts` now holding three layouts (`layout` dagre / `forceLayout` d3 / `radialLayout` none), and `wiki_read.citing_pages`.

- [ ] **Step 3: Update `NEXT.md`**

Move phase 4 from "in progress" to done; state phase 5 (ask & research) is next; refresh the branch and suite counts; remove §5 item 1 now that human-accepted merges are recorded.

- [ ] **Step 4: Full verification**

```bash
~/venvs/coscience/bin/python -m pytest -q
cd frontend && npx vitest run && npx tsc --noEmit && npm run build
```

Expected: backend exit 0, frontend all pass, `tsc` exit 0, build clean. Record the actual counts in `NEXT.md` rather than estimating them.

- [ ] **Step 5: Commit**

```bash
git add docs/knowledge-charter.md docs/knowledge/NEXT.md
git commit -m "docs(wiki): phase 4 record — deviations, seams and handoff"
```
