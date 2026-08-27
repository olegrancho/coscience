# Program Wiki Phase 2 (Browse) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** You can read a program's wiki in the dashboard and curate it — mark a page verified, set its lifecycle status, and write its protected `# Human notes` — with every claim clickable back to the result or artifact it came from.

**Architecture:** A new pure module `wiki_read.py` shapes bundle pages into API payloads (summary, page detail with backlinks and typed relations, keyword search). `Service` gains wiki methods that do the IO and the containment checks; `http_api.py` exposes them on the gated `api` router. The frontend gets one pure, unit-tested module `wikiPage.ts` (link rewriting + outline) and one view `WikiView.tsx` laid out in three panes, following `ArtifactDetail.tsx`'s react-query idiom.

**Tech Stack:** Python 3.11+, FastAPI, `fastapi.testclient`, pytest. React 18, react-router-dom, `@tanstack/react-query`, react-markdown via `components/Md.tsx`, vitest + `@testing-library/react`.

**Spec:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — §11 (API and UI) and §12 (modules, seams, testing) are the binding sections. §6.1 defines the trust tiers, §6.2 the protected section, §7 the relation vocabulary. Where this plan and the spec disagree, **the spec wins** — except for the five deviations recorded below, which are deliberate and argued.

**Predecessor:** phase 1 is complete on this branch (`feat/program-wiki`). Read `docs/knowledge-charter.md` §2 for what the first live run revealed, and `docs/knowledge/phase-1-record/final-fix-report.md` for the fixes and the residual risks. Do not re-litigate phase 1's rulings; `phase-1-record/decision-log.md` records them.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python ≥ 3.11.** `X | None`, `StrEnum`, and `from __future__ import annotations` at the top of every new module.
- **Runtime is Linux-only** (`/proc`, `os.killpg`, `fcntl`). Do not add Windows fallbacks.
- **Tests:** `~/venvs/coscience/bin/python -m pytest`. Plain `python` is not on PATH. Frontend: `cd frontend && npm test`.
- **The unit suite never calls a live LLM.** No task in this plan launches an agent. The one endpoint that *starts* a run (`POST /wiki/run`) must be tested against a fake, exactly as `wiki.beat` is.
- **The seam rule.** Logic modules are pure with no IO. `wiki_read.py` is pure: it takes already-loaded `wiki_okf.Page` objects and plain dicts, and returns plain data. All bundle IO stays in `wiki_store`; all substrate writes stay in `Service`. `wiki_lint.run_lint` remains the single documented exception.
- **Every method returns JSON-serialisable plain data** (`service.py`'s module docstring) so the MCP and HTTP layers can hand results straight to clients. No dataclasses, no `Path`, no `set` in a return value.
- **Frozen vocabularies.** 12 relation types (`is_a, part_of, requires, enables, implements, exemplifies, measures, causally_precedes, contradicts, refines, replaces, extends`) and 5 page types (`Concept, Entity, Synthesis, Source, Question`). Adding one is a spec change, not an implementation detail.
- **OKF tolerance.** Parsers tolerate unknown types, unknown keys and broken links; they never raise and never drop unknown keys. A page round-tripped through a write must not silently lose frontmatter the writer did not recognise.
- **Containment.** `{slug}` is a path segment and a traversal primitive. Every slug→path resolution must be containment-checked against the bundle root *after* symlink resolution, in the style of `Service._guarded_file` (`service.py:1327-1336`): resolve, then `is_relative_to`, then raise `NotFoundError`. Never string-compare.
- **Trust is derived, never stored.** No `verified` key → `unverified`; verified only by non-`human:` actors → `machine-confirmed`; any `human:` actor → `human-reviewed`. `status` is the orthogonal lifecycle axis (`draft|stable|deprecated`).
- **`# Human notes` is protected.** It is written only through its dedicated endpoint. Nothing else in this plan may rewrite it.
- **Stage explicit paths, never `git add -A`.** The carried-over frontend work this plan originally had to avoid (`styles.css`, `ProgramDetail.tsx`, `SprintDetail.tsx`, `PageToc.tsx`) **landed on `main` as `ee062d2` on 2026-08-21**, so there is no longer anything untouchable in the tree — but the habit stands, because a substrate or a stray runtime file wandering into a commit is how this branch's history gets muddied.
- **`@testing-library/user-event` is NOT a dependency.** `frontend/package.json` has only `@testing-library/react` and `vitest`; every existing test drives interaction with `fireEvent` (`CapacityModal.test.tsx:50`). Use `fireEvent.click` / `fireEvent.change`. Note `fireEvent.change` sets a value in one event rather than keystroke by keystroke, so a search box receives the whole query at once and needs no `clear` before a re-type.
- **Follow the dashboard's component conventions.** Every existing view is built from Mantine (`Card`, `Stack`, `Group`, `Text`, `Button`, `TextInput`, `Select`, `Textarea`) with `cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }`, an `.eyebrow` label per card, and `<Loader color="machine" />` while loading. A raw-HTML view would be the only one in the app. Component tests must wrap in `MantineProvider` and stub `window.matchMedia` / `window.ResizeObserver`, exactly as `ProgramDetail.test.tsx:10-18` does — Mantine touches both and jsdom has neither.
- **Never commit or push without the human's explicit approval.** Each task's commit step means "prepare and ask", not "push".
- **Do not touch the graph.** `/wiki/graph`, `wiki_graph.py` and `WikiGraphView` are phase 4. `POST /wiki/run {kind: lint}` exists here as an endpoint, but agent lint *mode* is phase 3.

---

## Deviations from the spec, and why

These five are deliberate. Each is a place a literal reading of §11–§12 would produce a defect or a collision.

**D1 — Pages are addressed by bundle path, not by bare slug.** §11.1 writes `/wiki/pages/{slug}`. But `wiki_okf.Page.slug` (`wiki_okf.py:77-79`) is the *filename stem*, so `concepts/auth-gate.md` and `entities/auth-gate.md` both have slug `auth-gate` and the route is ambiguous the moment an agent names a concept and an entity the same thing — which the first live run came within one page of doing. Every page route in this plan therefore takes the bundle-relative path without `.md`, declared `{slug:path}` (the same `:path` converter `http_api.py:548` already uses for nested artifact file names). `concepts/auth-gate` is the address; the containment check is what makes the `:path` converter safe.

**D2 — A new pure module `wiki_read.py`, rather than growing `service.py`.** `service.py` is already 1544 lines. §12's module table lists the phase-1 and phase-4 modules and is silent on the read side; putting the shaping logic in a pure module keeps it unit-testable without a substrate, which is exactly what §12's testing strategy asks for.

**D3 — WITHDRAWN.** This slot previously argued for a separate `frontend/src/wiki.css`, because `styles.css` carried uncommitted work. That work landed in `ee062d2`, so the reason is gone — and the repo's convention is one stylesheet (`ee062d2` itself put `.page-toc` in `styles.css`). Wiki styles go in `frontend/src/styles.css`, appended in the same commented-section idiom the file already uses. No new CSS file.

**D4 — The outline is rendered in the right pane, not by reusing `PageToc.tsx`.** §11.2 names `PageToc.tsx` for the right pane. Now that the component is real and readable (`ee062d2`), it turns out to be the wrong fit — and for a better reason than the original "it is untracked":

- It is `position: fixed` in the **left** gutter (`styles.css`: `left: calc(232px + 16px)`) and portals to `document.body`. The wiki's left column is already the page tree, so mounting it would put two navigations in the same gutter, one on top of the other.
- It hides itself under 1500px wide (`@media (max-width: 1500px) { display: none }`), so on a laptop the wiki's outline would silently vanish while the rest of the right pane stayed.
- Its scroll-spy observes elements by DOM id, and markdown-rendered headings have no ids — `Md.tsx` does not add them. Wiring it up would mean also overriding heading renderers to inject ids.

So the outline stays an in-flow list in the right pane, next to backlinks and relations, which is what §11.2 actually describes the pane as containing. `wikiPage.outline()` remains the parser — nothing else in the codebase extracts headings from markdown, and `PageToc` takes pre-built entries rather than deriving them. **If you later want the wiki to have a gutter ToC like the other pages, `outline()` already returns `{id, label}`-shaped data that feeds `PageToc` directly** — that is a small follow-up, not a rewrite.

**D5 — RESOLVED, Task 15 is unblocked.** This previously blocked Task 15 because `ProgramDetail.tsx` carried uncommitted work. `ee062d2` committed it, so the "open wiki →" link is now an ordinary edit to a clean file. Task 15 executes normally.

---

## File structure

| File | Responsibility |
|---|---|
| `src/coscience/wiki_read.py` | **NEW, pure.** Summary shaping, trust derivation, page detail (backlinks, grouped relations, provenance refs), keyword search. No IO. |
| `src/coscience/service.py` | Wiki read + curation methods. Owns slug→path containment, all bundle writes, force-run and unquarantine. |
| `src/coscience/http_api.py` | 12 endpoints on the gated `api` router, delegating to `Service`, translating `NotFoundError` → 404 and `ValueError` → 400. |
| `frontend/src/api.ts` | Wiki types and client methods. |
| `frontend/src/components/wikiPage.ts` | **NEW, pure.** Internal-link rewriting for client-side routing, heading outline extraction, provenance chip hrefs. |
| `frontend/src/views/WikiView.tsx` | **NEW.** The three-pane browse view, built from Mantine like every other view. |
| `frontend/src/styles.css` | Appended `.wiki-*` section (see D3). |
| `frontend/src/App.tsx` | The `/programs/:id/wiki` route. |
| `frontend/src/views/ProgramDetail.tsx` | The "open wiki →" link (Task 15). |

Tests: `tests/test_wiki_read.py`, `tests/test_wiki_read_page.py`, `tests/test_wiki_read_search.py`, `tests/test_service_wiki_read.py`, `tests/test_service_wiki_curate.py`, `tests/test_service_wiki_runs.py`, `tests/test_http_wiki_read.py`, `tests/test_http_wiki_write.py`, `frontend/src/components/wikiPage.test.ts`, `frontend/src/views/WikiView.test.tsx`.

---

### Task 1: `wiki_read.summary` — counts, trust tiers, run state

**Files:**
- Create: `src/coscience/wiki_read.py`
- Test: `tests/test_wiki_read.py`

**Interfaces:**
- Consumes: `wiki_okf.Page` (`wiki_okf.py:59-88`), the state dict shape from `wiki_store.load_state` (`wiki_store.py:331`).
- Produces:
  - `trust_tier(page: wiki_okf.Page) -> str` → `"unverified" | "machine-confirmed" | "human-reviewed"`
  - `summary(pages: list[wiki_okf.Page], state: dict, pending: int, lint_counts: dict, index_md: str) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_read.py
from coscience import wiki_okf, wiki_read


def _page(path, **kw):
    return wiki_okf.Page(path=path, type=kw.pop("type", "Concept"), **kw)


def test_trust_tier_is_derived_from_verified_not_from_status():
    # Spec 6.1: status is lifecycle, trust is derived. A stable page nobody
    # checked is still unverified — that is the whole point of separating them.
    assert wiki_read.trust_tier(_page("concepts/a.md", status="stable")) == "unverified"
    machine = _page("concepts/b.md", verified=[{"by": "coscience-wiki/claude-sonnet-5"}])
    assert wiki_read.trust_tier(machine) == "machine-confirmed"
    human = _page("concepts/c.md", verified=[{"by": "coscience-wiki/x"}, {"by": "human:oleg"}])
    assert wiki_read.trust_tier(human) == "human-reviewed"


def test_summary_counts_by_type_and_by_trust():
    pages = [_page("concepts/a.md"), _page("concepts/b.md"),
             _page("entities/c.md", type="Entity", verified=[{"by": "human:oleg"}])]
    out = wiki_read.summary(pages, state={}, pending=3, lint_counts={}, index_md="# I")
    assert out["counts"] == {"Concept": 2, "Entity": 1}
    assert out["trust"] == {"unverified": 2, "machine-confirmed": 0, "human-reviewed": 1}
    assert out["pending"] == 3
    assert out["index_md"] == "# I"


def test_summary_carries_run_state_through_verbatim():
    state = {"last_run": {"id": "r0001", "status": "ok"}, "run": {"id": "r0002"},
             "ingests_since_lint": 2, "quarantined": ["result:r9"]}
    out = wiki_read.summary([], state, pending=0, lint_counts={"error": 1, "warn": 4},
                            index_md="")
    assert out["last_run"] == {"id": "r0001", "status": "ok"}
    assert out["run"] == {"id": "r0002"}
    assert out["ingests_since_lint"] == 2
    assert out["quarantined"] == ["result:r9"]
    assert out["lint"] == {"error": 1, "warn": 4}


def test_summary_of_an_empty_bundle_is_all_zeros_not_an_error():
    out = wiki_read.summary([], {}, 0, {}, "")
    assert out["counts"] == {}
    assert out["trust"] == {"unverified": 0, "machine-confirmed": 0, "human-reviewed": 0}
    assert out["run"] is None and out["last_run"] is None


def test_summary_tolerates_an_unknown_page_type():
    # OKF requires consumers to tolerate types we did not specify.
    out = wiki_read.summary([_page("x/y.md", type="Protocol")], {}, 0, {}, "")
    assert out["counts"] == {"Protocol": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_read.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_read'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coscience/wiki_read.py
"""Shaping bundle pages into API payloads. Pure: no IO, no substrate.

Everything here takes already-loaded pages and plain dicts and returns plain
JSON-serialisable data, so the whole read surface is unit-testable without a
substrate on disk. Bundle IO lives in wiki_store; writes live in Service.
"""
from __future__ import annotations

from coscience import wiki_okf

TIERS = ("unverified", "machine-confirmed", "human-reviewed")


def trust_tier(page: wiki_okf.Page) -> str:
    """Spec 6.1: trust is *derived* from `verified`, never stored. `status` is the
    orthogonal lifecycle axis — a `stable` page nobody checked is unverified."""
    actors = [str(v.get("by", "")) for v in (page.verified or [])]
    if any(a.startswith("human:") for a in actors):
        return "human-reviewed"
    return "machine-confirmed" if actors else "unverified"


def summary(pages: list[wiki_okf.Page], state: dict, pending: int,
            lint_counts: dict, index_md: str) -> dict:
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
        "index_md": index_md,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_read.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_read.py tests/test_wiki_read.py
git commit -m "feat(wiki): wiki_read.summary — counts, derived trust tiers, run state"
```

---

### Task 2: `wiki_read.page_detail` — backlinks, grouped relations, provenance

**Files:**
- Modify: `src/coscience/wiki_read.py`
- Test: `tests/test_wiki_read_page.py`

**Interfaces:**
- Consumes: `trust_tier` from Task 1; `wiki_okf.body_links` and `wiki_okf.wikilinks` (`wiki_okf.py:199`); `wiki_okf.Source` / `wiki_okf.Relation`.
- Produces:
  - `page_detail(page: wiki_okf.Page, pages: list[wiki_okf.Page]) -> dict` with keys `path, slug, type, title, description, status, tags, aliases, trust, verified, stale_after, body, relations, backlinks, sources, outline_source`
  - `provenance_ref(source_id: str, resource: str) -> dict` → `{"kind": "result"|"sprint"|"artifact"|"unknown", "id": str, "href": str, "resource": str}`
- `relations` is a list of `{"type": str, "target": str, "title": str, "exists": bool, "confidence": str, "source": str}`, ordered by the frozen §7 vocabulary then by target.
- `backlinks` is a list of `{"path": str, "title": str, "type": str, "typed": list[str]}` — `typed` names the relation types pointing here, empty for a plain body link.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_read_page.py
from coscience import wiki_okf, wiki_read


def _page(path, body="", relations=(), **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    return wiki_okf.Page(path=path, body=body,
                         relations=[wiki_okf.Relation(**r) for r in relations], **kw)


def test_relations_report_whether_the_target_exists():
    # A broken relation must render as broken, not vanish: OKF tolerates dangling
    # links and the reader needs to see one.
    a = _page("concepts/a.md", relations=[
        {"type": "requires", "target": "/concepts/b.md", "confidence": "high", "source": "c1"},
        {"type": "requires", "target": "/concepts/gone.md", "source": "c1"}])
    b = _page("concepts/b.md", title="B")
    out = wiki_read.page_detail(a, [a, b])
    by_target = {r["target"]: r for r in out["relations"]}
    assert by_target["concepts/b.md"]["exists"] is True
    assert by_target["concepts/b.md"]["title"] == "B"
    assert by_target["concepts/gone.md"]["exists"] is False


def test_relations_are_ordered_by_the_frozen_vocabulary():
    a = _page("concepts/a.md", relations=[
        {"type": "extends", "target": "/concepts/b.md", "source": "c1"},
        {"type": "is_a", "target": "/concepts/c.md", "source": "c1"}])
    out = wiki_read.page_detail(a, [a])
    assert [r["type"] for r in out["relations"]] == ["is_a", "extends"]


def test_backlinks_find_both_body_links_and_typed_relations():
    target = _page("concepts/target.md")
    plain = _page("concepts/plain.md", body="see [t](/concepts/target.md)")
    typed = _page("concepts/typed.md", body="see [t](/concepts/target.md)",
                  relations=[{"type": "refines", "target": "/concepts/target.md",
                              "source": "c1"}])
    out = wiki_read.page_detail(target, [target, plain, typed])
    by_path = {b["path"]: b for b in out["backlinks"]}
    assert by_path["concepts/plain.md"]["typed"] == []
    assert by_path["concepts/typed.md"]["typed"] == ["refines"]


def test_a_page_does_not_backlink_to_itself():
    a = _page("concepts/a.md", body="see [me](/concepts/a.md)")
    assert wiki_read.page_detail(a, [a])["backlinks"] == []


def test_backlinks_see_a_wikilink_too():
    # Phase 1's live run wrote [[concepts/x]]; until lint rewrites them the reader
    # must still see the inbound link.
    target = _page("concepts/target.md")
    src = _page("concepts/src.md", body="see [[concepts/target]]")
    out = wiki_read.page_detail(target, [target, src])
    assert [b["path"] for b in out["backlinks"]] == ["concepts/src.md"]


def test_sources_become_provenance_refs_by_kind():
    a = _page("concepts/a.md")
    a.sources = [wiki_okf.Source(id="c1", resource="/results/r7.md", title="R7"),
                 wiki_okf.Source(id="c2", resource="/programs/p1/artifacts/fig/v1",
                                 title="fig")]
    out = wiki_read.page_detail(a, [a])
    refs = {s["id"]: s for s in out["sources"]}
    assert refs["c1"]["kind"] == "result" and refs["c1"]["href"] == "/results/r7"
    assert refs["c2"]["kind"] == "artifact"
    assert refs["c2"]["href"] == "/programs/p1/artifacts/fig"


def test_an_unrecognised_resource_is_marked_unknown_not_dropped():
    a = _page("concepts/a.md")
    a.sources = [wiki_okf.Source(id="c1", resource="https://example.org/x", title="x")]
    out = wiki_read.page_detail(a, [a])
    assert out["sources"][0]["kind"] == "unknown"
    assert out["sources"][0]["href"] == ""
    assert out["sources"][0]["resource"] == "https://example.org/x"


def test_page_detail_carries_trust_and_the_protected_section_verbatim():
    a = _page("concepts/a.md", body="# Definition\n\nd\n\n# Human notes\n\nkeep me\n",
              verified=[{"by": "human:oleg", "at": 1.0}])
    out = wiki_read.page_detail(a, [a])
    assert out["trust"] == "human-reviewed"
    assert out["human_notes"] == "keep me"
    assert "# Human notes" in out["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_read_page.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_read' has no attribute 'page_detail'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/wiki_read.py`:

```python
# The frozen 12 of spec 7, in the order a reader wants them: what this thing IS,
# then what it is part of, then what it needs, then the softer commentary.
_REL_ORDER = ("is_a", "part_of", "requires", "enables", "implements", "exemplifies",
              "measures", "causally_precedes", "contradicts", "refines", "replaces",
              "extends")


def _norm_target(target: str) -> str:
    return (target or "").split("#", 1)[0].strip().lstrip("/")


def provenance_ref(source_id: str, resource: str) -> dict:
    """A source's `resource` as a dashboard link. Sources are pointers into the
    platform (spec 5) — this is the payoff for building the wiki inside it.

    Anything unrecognised is reported as `unknown` with an empty href rather than
    dropped: a page citing something we cannot route to is still citing it, and
    hiding that would look like the claim had no source at all."""
    r = (resource or "").strip()
    body = r.lstrip("/")
    parts = body.split("/")
    kind, href = "unknown", ""
    if parts[0] == "results" and len(parts) >= 2:
        kind, href = "result", f"/results/{parts[1].removesuffix('.md')}"
    elif parts[0] == "sprints" and len(parts) >= 2:
        kind, href = "sprint", f"/sprints/{parts[1]}"
    elif parts[0] == "programs" and len(parts) >= 4 and parts[2] == "artifacts":
        kind, href = "artifact", f"/programs/{parts[1]}/artifacts/{parts[3]}"
    return {"id": source_id, "kind": kind, "href": href, "resource": r}


def page_detail(page: wiki_okf.Page, pages: list[wiki_okf.Page]) -> dict:
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
        targets = {_norm_target(t) for t in wiki_okf.body_links(other.body)}
        targets |= {_norm_target(w) for w in wiki_okf.wikilinks(other.body)}
        targets |= {f"{_norm_target(w)}.md" for w in wiki_okf.wikilinks(other.body)}
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
        "human_notes": page.section("Human notes"),
        "relations": relations, "backlinks": backlinks,
        "sources": [provenance_ref(s.id, s.resource) | {"title": s.title}
                    for s in page.sources],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_read_page.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_read.py tests/test_wiki_read_page.py
git commit -m "feat(wiki): wiki_read.page_detail — backlinks, ordered relations, provenance refs"
```

---

### Task 3: `wiki_read.search` — keyword search over the bundle

**Files:**
- Modify: `src/coscience/wiki_read.py`
- Test: `tests/test_wiki_read_search.py`

**Interfaces:**
- Produces: `search(pages: list[wiki_okf.Page], q: str, limit: int = 50) -> list[dict]`, each hit `{"path", "title", "type", "trust", "score", "excerpt"}`, best first.

Scoring is deliberately crude and explainable — title hit beats alias hit beats body hit — because a smarter ranker we cannot explain is worse than a dumb one we can. No index, no stemming: bundles are hundreds of pages, not millions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_read_search.py
from coscience import wiki_okf, wiki_read


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    return wiki_okf.Page(path=path, **kw)


def test_a_title_hit_outranks_a_body_hit():
    title = _page("concepts/lease.md", title="Compute lease")
    body = _page("concepts/other.md", title="Other", body="mentions a compute lease here")
    hits = wiki_read.search([body, title], "compute lease")
    assert [h["path"] for h in hits] == ["concepts/lease.md", "concepts/other.md"]


def test_an_alias_hit_is_found_and_ranks_between_title_and_body():
    aliased = _page("concepts/a.md", title="Compute lease", aliases=["leases.json"])
    hits = wiki_read.search([aliased], "leases.json")
    assert [h["path"] for h in hits] == ["concepts/a.md"]


def test_search_is_case_insensitive_and_returns_an_excerpt_around_the_hit():
    # Filler on both sides has to exceed the excerpt window (±80), or the window
    # covers the whole body and there is nothing to truncate. The first draft of
    # this test used 60 and failed for that reason, not because search was wrong.
    p = _page("concepts/a.md", title="A",
              body="x" * 200 + " HYDROLYSIS matters " + "y" * 200)
    hit = wiki_read.search([p], "hydrolysis")[0]
    assert "HYDROLYSIS" in hit["excerpt"]
    assert len(hit["excerpt"]) < len(p.body)
    assert hit["excerpt"].startswith("…") and hit["excerpt"].endswith("…")


def test_a_short_body_is_excerpted_whole_without_ellipses():
    p = _page("concepts/a.md", title="A", body="a lease is held while awake")
    hit = wiki_read.search([p], "lease")[0]
    assert hit["excerpt"] == "a lease is held while awake"


def test_a_query_matching_nothing_returns_nothing():
    assert wiki_read.search([_page("concepts/a.md")], "zzzz") == []


def test_a_blank_query_returns_nothing_rather_than_everything():
    # A blank box must not dump the whole bundle through the wire.
    assert wiki_read.search([_page("concepts/a.md")], "   ") == []


def test_search_respects_the_limit():
    pages = [_page(f"concepts/p{i}.md", title="lease") for i in range(10)]
    assert len(wiki_read.search(pages, "lease", limit=3)) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_read_search.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_read' has no attribute 'search'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/wiki_read.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_read_search.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_read.py tests/test_wiki_read_search.py
git commit -m "feat(wiki): wiki_read.search — explainable title/alias/body ranking"
```

---

### Task 4: `Service` read methods and the containment guard

**Files:**
- Modify: `src/coscience/service.py` (append a wiki section at the end of the class)
- Test: `tests/test_service_wiki_read.py`

**Interfaces:**
- Consumes: `wiki_read.summary`, `wiki_read.page_detail`, `wiki_read.search`; `wiki_store.iter_pages` / `read_page` / `load_state` / `pending_objects` / `bundle_dir` (`wiki_store.py:145-371`); `wiki_lint.run_lint` (`wiki_lint.py:400`).
- Produces:
  - `Service.wiki_page_path(program_id: str, slug: str) -> Path` — the guard. Raises `NotFoundError`.
  - `Service.wiki_summary(program_id) -> dict`
  - `Service.list_wiki_pages(program_id) -> list[dict]`
  - `Service.get_wiki_page(program_id, slug) -> dict`
  - `Service.search_wiki(program_id, q, limit=50) -> list[dict]`
  - `Service.wiki_log(program_id) -> str`
  - `Service.wiki_lint_report(program_id) -> dict`

`slug` here is the bundle path without `.md` (D1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_wiki_read.py
import pytest

from coscience import wiki_okf, wiki_store
from coscience.service import NotFoundError, Service


def _write(substrate, path, body="x" * 400, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(path=path, body=body, **kw))


def _svc(substrate):
    return Service(substrate.repo_root)


def test_summary_and_page_list_of_a_seeded_bundle(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/a.md")
    _write(substrate, "entities/b.md", type="Entity")
    svc = _svc(substrate)
    assert _svc(substrate).wiki_summary("p1")["counts"] == {"Concept": 1, "Entity": 1}
    assert sorted(p["path"] for p in svc.list_wiki_pages("p1")) == \
        ["concepts/a.md", "entities/b.md"]


def test_a_page_is_addressed_by_its_bundle_path_not_its_bare_slug(wiki_bundle):
    # Two pages can share a filename stem; the path is what disambiguates them.
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/auth-gate.md", title="Concept side")
    _write(substrate, "entities/auth-gate.md", type="Entity", title="Entity side")
    svc = _svc(substrate)
    assert svc.get_wiki_page("p1", "concepts/auth-gate")["title"] == "Concept side"
    assert svc.get_wiki_page("p1", "entities/auth-gate")["title"] == "Entity side"


def test_a_missing_page_is_a_not_found(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        _svc(substrate).get_wiki_page("p1", "concepts/nope")


@pytest.mark.parametrize("slug", [
    "../../../etc/passwd",
    "concepts/../../../../etc/passwd",
    "/etc/passwd",
    "concepts/../../programs/p1/program",
])
def test_a_traversing_slug_never_escapes_the_bundle(wiki_bundle, slug):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        _svc(substrate).wiki_page_path("p1", slug)


def test_a_symlink_out_of_the_bundle_is_refused(wiki_bundle, tmp_path):
    substrate, _ = wiki_bundle
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    link = wiki_store.bundle_dir(substrate, "p1") / "concepts" / "escape.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(NotFoundError):
        _svc(substrate).wiki_page_path("p1", "concepts/escape")


def test_search_and_log_and_lint_come_back_as_plain_data(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/lease.md", title="Compute lease")
    (wiki_store.bundle_dir(substrate, "p1") / "log.md").write_text("# Log\n\n- line\n")
    svc = _svc(substrate)
    assert [h["path"] for h in svc.search_wiki("p1", "compute")] == ["concepts/lease.md"]
    assert "- line" in svc.wiki_log("p1")
    report = svc.wiki_lint_report("p1")
    assert set(report) >= {"counts", "findings"}
    assert isinstance(report["findings"], list)


def test_summary_of_a_program_with_no_bundle_is_empty_not_an_error(substrate):
    from coscience.models import Program
    substrate.save_program(Program(id="p9", title="P9", goals="g"))
    out = _svc(substrate).wiki_summary("p9")
    assert out["pages"] == 0 and out["counts"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_read.py -v`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'wiki_summary'`

- [ ] **Step 3: Write minimal implementation**

Append inside `class Service` in `src/coscience/service.py`:

```python
    # --- wiki (phase 2: read) -------------------------------------------------

    def wiki_page_path(self, program_id: str, slug: str) -> Path:
        """Guarded path to one bundle page. `slug` is the bundle-relative path
        without `.md` (`concepts/auth-gate`).

        A slug arrives from a URL segment, so it is a traversal primitive: resolve
        first, then prove containment, exactly as _guarded_file does for artifact
        versions. Symlink resolution happens before the check, which is why this
        cannot be a string comparison."""
        from coscience import wiki_store
        try:
            root = wiki_store.bundle_dir(self.substrate, program_id).resolve()
            path = (root / f"{slug}.md").resolve()
        except (ValueError, OSError):
            raise NotFoundError(slug)
        if not path.is_file() or not path.is_relative_to(root):
            raise NotFoundError(slug)
        return path

    def _wiki_pages(self, program_id: str):
        from coscience import wiki_store
        return wiki_store.iter_pages(self.substrate, program_id)

    def wiki_summary(self, program_id: str) -> dict:
        from coscience import wiki_read, wiki_store
        pages = self._wiki_pages(program_id)
        state = wiki_store.load_state(self.substrate, program_id)
        pending = len(wiki_store.pending_objects(
            self.substrate, program_id, state.get("ingested") or {},
            set(state.get("quarantined") or [])))
        counts = self.wiki_lint_report(program_id)["counts"]
        try:
            index_md = (wiki_store.bundle_dir(self.substrate, program_id)
                        / "index.md").read_text()
        except OSError:
            index_md = ""
        return wiki_read.summary(pages, state, pending, counts, index_md)

    def list_wiki_pages(self, program_id: str) -> list[dict]:
        from coscience import wiki_read
        return [{"path": p.path, "slug": p.slug, "type": p.type,
                 "title": p.title or p.slug, "status": p.status,
                 "trust": wiki_read.trust_tier(p), "stale_after": p.stale_after,
                 "tags": list(p.tags)}
                for p in sorted(self._wiki_pages(program_id), key=lambda p: p.path)]

    def get_wiki_page(self, program_id: str, slug: str) -> dict:
        from coscience import wiki_read, wiki_store
        self.wiki_page_path(program_id, slug)          # guard before reading
        page = wiki_store.read_page(self.substrate, program_id, f"{slug}.md")
        if page is None:
            raise NotFoundError(slug)
        return wiki_read.page_detail(page, self._wiki_pages(program_id))

    def search_wiki(self, program_id: str, q: str, limit: int = 50) -> list[dict]:
        from coscience import wiki_read
        return wiki_read.search(self._wiki_pages(program_id), q, limit=limit)

    def wiki_log(self, program_id: str) -> str:
        from coscience import wiki_store
        try:
            return (wiki_store.bundle_dir(self.substrate, program_id)
                    / "log.md").read_text()
        except OSError:
            return ""

    def wiki_lint_report(self, program_id: str) -> dict:
        """Live findings, never autofixed. `fix=True` here would mean a GET
        mutated the bundle."""
        from coscience import wiki_lint
        findings, _ = wiki_lint.run_lint(self.substrate, program_id, fix=False)
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return {"counts": counts,
                "findings": [{"rule": f.rule, "severity": f.severity,
                              "path": f.path, "message": f.message} for f in findings]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_read.py -v`
Expected: PASS (8 tests, one may skip on a filesystem without symlinks)

`wiki_lint.Finding` is `(rule, severity, path, message)` — verified, so the comprehension above is correct as written.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_read.py
git commit -m "feat(wiki): Service wiki read methods with a containment-guarded page path"
```

---

### Task 5: `Service` curation — verify, status, human notes

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_wiki_curate.py`

**Interfaces:**
- Produces:
  - `Service.verify_wiki_page(program_id, slug, by: str, now: float | None = None) -> dict`
  - `Service.set_wiki_page_status(program_id, slug, status: str) -> dict`
  - `Service.set_wiki_human_notes(program_id, slug, text: str) -> dict`
- All three return the fresh `page_detail` dict, and all three commit through `self.substrate.commit`.

`status` accepts only `draft|stable|deprecated` (spec §6.1) and raises `ValueError` otherwise. `by` must already be actor-formatted (`human:<id>`); the HTTP layer builds it from the session user.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_wiki_curate.py
import pytest

from coscience import wiki_okf, wiki_store
from coscience.service import NotFoundError, Service


def _write(substrate, path="concepts/a.md", body=None, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", "A")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path=path, body=body if body is not None else "# Definition\n\n" + "x" * 400, **kw))


def test_verify_appends_a_human_actor_and_flips_the_derived_tier(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    svc = Service(substrate.repo_root)
    assert svc.get_wiki_page("p1", "concepts/a")["trust"] == "unverified"
    out = svc.verify_wiki_page("p1", "concepts/a", by="human:oleg", now=10.0)
    assert out["trust"] == "human-reviewed"
    assert out["verified"][-1] == {"by": "human:oleg", "at": 10.0}


def test_verifying_twice_appends_rather_than_replacing(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    svc = Service(substrate.repo_root)
    svc.verify_wiki_page("p1", "concepts/a", by="human:a", now=1.0)
    out = svc.verify_wiki_page("p1", "concepts/a", by="human:b", now=2.0)
    assert [v["by"] for v in out["verified"]] == ["human:a", "human:b"]


def test_status_accepts_the_lifecycle_values_and_refuses_anything_else(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    svc = Service(substrate.repo_root)
    assert svc.set_wiki_page_status("p1", "concepts/a", "stable")["status"] == "stable"
    with pytest.raises(ValueError):
        svc.set_wiki_page_status("p1", "concepts/a", "verified")


def test_human_notes_are_written_into_the_protected_section(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate)
    out = Service(substrate.repo_root).set_wiki_human_notes(
        "p1", "concepts/a", "the lease id changes on wake")
    assert out["human_notes"] == "the lease id changes on wake"
    assert "# Human notes" in out["body"]


def test_writing_notes_replaces_only_that_section(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, body="# Definition\n\nkeep\n\n# Human notes\n\nold\n\n"
                           "# Open questions\n\nalso keep\n")
    out = Service(substrate.repo_root).set_wiki_human_notes("p1", "concepts/a", "new")
    assert out["human_notes"] == "new"
    assert "keep" in out["body"] and "also keep" in out["body"]
    assert "old" not in out["body"]


def test_notes_can_be_added_to_a_page_that_has_no_such_section_yet(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, body="# Definition\n\nd\n")
    out = Service(substrate.repo_root).set_wiki_human_notes("p1", "concepts/a", "added")
    assert out["human_notes"] == "added"


def test_curating_preserves_unknown_frontmatter_keys(wiki_bundle):
    # OKF conformance: a round-trip must not drop what we did not recognise.
    substrate, _ = wiki_bundle
    _write(substrate, extra={"custom_key": "keep me"})
    Service(substrate.repo_root).verify_wiki_page("p1", "concepts/a", by="human:o", now=1.0)
    page = wiki_store.read_page(substrate, "p1", "concepts/a.md")
    assert page.extra.get("custom_key") == "keep me"


def test_curating_a_missing_page_is_a_not_found(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).verify_wiki_page("p1", "concepts/nope", by="human:o")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_curate.py -v`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'verify_wiki_page'`

- [ ] **Step 3: Write minimal implementation**

Append inside `class Service`:

```python
    # --- wiki (phase 2: curation) --------------------------------------------

    _WIKI_STATUSES = ("draft", "stable", "deprecated")

    def _load_wiki_page(self, program_id: str, slug: str):
        from coscience import wiki_store
        self.wiki_page_path(program_id, slug)
        page = wiki_store.read_page(self.substrate, program_id, f"{slug}.md")
        if page is None:
            raise NotFoundError(slug)
        return page

    def _save_wiki_page(self, program_id: str, page, message: str) -> dict:
        from coscience import wiki_read, wiki_store
        wiki_store.write_page(self.substrate, program_id, page)
        self.substrate.commit(message)
        return wiki_read.page_detail(page, self._wiki_pages(program_id))

    def verify_wiki_page(self, program_id: str, slug: str, by: str,
                         now: float | None = None) -> dict:
        """Append one OKF `verified` entry. Appends rather than replaces: the trust
        tier is derived from the whole list, and who checked a page previously is
        part of its record."""
        page = self._load_wiki_page(program_id, slug)
        page.verified = list(page.verified or []) + [
            {"by": by, "at": float(now if now is not None else time.time())}]
        return self._save_wiki_page(program_id, page,
                                    f"wiki {program_id}: verified {slug} by {by}")

    def set_wiki_page_status(self, program_id: str, slug: str, status: str) -> dict:
        if status not in self._WIKI_STATUSES:
            raise ValueError(f"status must be one of {self._WIKI_STATUSES}: {status}")
        page = self._load_wiki_page(program_id, slug)
        page.status = status
        return self._save_wiki_page(program_id, page,
                                    f"wiki {program_id}: {slug} status {status}")

    def set_wiki_human_notes(self, program_id: str, slug: str, text: str) -> dict:
        """Replace the protected `# Human notes` section, and nothing else.

        This is the only writer of that section (spec 6.2) — agents must never
        touch it, which is why it has an endpoint of its own instead of free-form
        page editing."""
        page = self._load_wiki_page(program_id, slug)
        page.body = _replace_section(page.body, "Human notes", text)
        return self._save_wiki_page(program_id, page,
                                    f"wiki {program_id}: human notes on {slug}")
```

And at module level in `service.py`, beside the other helpers:

```python
def _replace_section(body: str, heading: str, text: str) -> str:
    """Swap the body text under `# <heading>`, appending the section if absent.

    Mirrors wiki_okf.Page.section's view of a section — from its heading to the
    next `# ` — so a read after a write returns exactly what was written."""
    import re
    marks = [(m.group(1), m.start(), m.end())
             for m in re.finditer(r"(?m)^# +(.+?)\s*$", body)]
    block = f"# {heading}\n\n{text.strip()}\n"
    for i, (name, start, _end) in enumerate(marks):
        if name.strip().lower() == heading.strip().lower():
            stop = marks[i + 1][1] if i + 1 < len(marks) else len(body)
            return body[:start] + block + ("\n" + body[stop:] if stop < len(body) else "")
    return body.rstrip("\n") + "\n\n" + block
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_curate.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the wiki suite to prove lint still agrees with the writer**

Run: `~/venvs/coscience/bin/python -m pytest tests/ -k wiki -v`
Expected: PASS. In particular `human-notes` lint rules must not start firing on pages this task wrote.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_curate.py
git commit -m "feat(wiki): Service curation — verify, lifecycle status, protected human notes"
```

---

### Task 6: `Service.delete_wiki_page` — dropping inbound relations

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_wiki_delete.py`

**Interfaces:**
- Produces: `Service.delete_wiki_page(program_id, slug) -> dict` → `{"deleted": str, "relations_dropped": [{"path": str, "type": str}]}`

§11.1 requires the delete to drop inbound relations. It must not rewrite bodies: a dangling *body* link is visible and honest, whereas a dangling typed relation silently violates the containment invariant `rel/no-link` is built to enforce.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_wiki_delete.py
import pytest

from coscience import wiki_okf, wiki_store
from coscience.service import NotFoundError, Service


def _write(substrate, path, relations=(), body=None):
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path=path, type="Concept", title=path.rsplit("/", 1)[-1][:-3],
        body=body if body is not None else "x" * 400,
        relations=[wiki_okf.Relation(**r) for r in relations]))


def test_delete_removes_the_page_and_reports_it(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/a.md")
    out = Service(substrate.repo_root).delete_wiki_page("p1", "concepts/a")
    assert out["deleted"] == "concepts/a.md"
    assert wiki_store.read_page(substrate, "p1", "concepts/a.md") is None


def test_delete_drops_typed_relations_pointing_at_the_dead_page(wiki_bundle):
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/gone.md")
    _write(substrate, "concepts/keeper.md", relations=[
        {"type": "requires", "target": "/concepts/gone.md", "source": "c1"},
        {"type": "is_a", "target": "/concepts/other.md", "source": "c1"}])
    out = Service(substrate.repo_root).delete_wiki_page("p1", "concepts/gone")
    assert out["relations_dropped"] == [{"path": "concepts/keeper.md",
                                        "type": "requires"}]
    keeper = wiki_store.read_page(substrate, "p1", "concepts/keeper.md")
    assert [r.target for r in keeper.relations] == ["/concepts/other.md"]


def test_delete_leaves_body_links_alone(wiki_bundle):
    # A visible dangling link is honest; silently editing someone's prose is not.
    substrate, _ = wiki_bundle
    _write(substrate, "concepts/gone.md")
    _write(substrate, "concepts/keeper.md", body="see [g](/concepts/gone.md) " + "x" * 400)
    Service(substrate.repo_root).delete_wiki_page("p1", "concepts/gone")
    keeper = wiki_store.read_page(substrate, "p1", "concepts/keeper.md")
    assert "(/concepts/gone.md)" in keeper.body


def test_deleting_a_missing_page_is_a_not_found(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).delete_wiki_page("p1", "concepts/nope")


def test_delete_cannot_be_talked_into_leaving_the_bundle(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).delete_wiki_page("p1", "../../../etc/passwd")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_delete.py -v`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'delete_wiki_page'`

- [ ] **Step 3: Write minimal implementation**

Append inside `class Service`:

```python
    def delete_wiki_page(self, program_id: str, slug: str) -> dict:
        """Delete a page and drop every typed relation pointing at it.

        Body links are deliberately left dangling: a broken link a reader can see
        is honest, while a dangling typed relation silently breaks the containment
        invariant rel/no-link exists to enforce."""
        from coscience import wiki_store
        path = self.wiki_page_path(program_id, slug)
        rel = f"{slug}.md"
        dropped = []
        for other in self._wiki_pages(program_id):
            if other.path == rel:
                continue
            keep = [r for r in other.relations
                    if (r.target or "").split("#", 1)[0].strip().lstrip("/") != rel]
            if len(keep) != len(other.relations):
                dropped += [{"path": other.path, "type": r.type}
                            for r in other.relations if r not in keep]
                other.relations = keep
                wiki_store.write_page(self.substrate, program_id, other)
        path.unlink()
        self.substrate.commit(f"wiki {program_id}: deleted {rel}")
        return {"deleted": rel, "relations_dropped": dropped}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_delete.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_delete.py
git commit -m "feat(wiki): Service.delete_wiki_page drops inbound typed relations"
```

---

### Task 7: `Service` run controls — force a run, clear quarantine

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_wiki_runs.py`

**Interfaces:**
- Produces:
  - `Service.run_wiki(program_id, kind: str = "ingest", agent=None) -> dict` → `{"line": str}`
  - `Service.unquarantine_wiki(program_id) -> dict` → `{"cleared": list[str]}`

`run_wiki` delegates to `wiki.beat` with the usage gate forced open — the human clicked the button, which is a stronger signal than the gate — and takes an injectable `agent` so the suite never launches a process. `kind="lint"` sets `ingests_since_lint` to the threshold so the beat's own state machine chooses a lint run, rather than adding a second way to decide what a run is.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_wiki_runs.py
import pytest

from coscience import wiki_store
from coscience.models import Result, Sprint, SprintStatus
from coscience.service import Service
from tests.test_wiki_beat import FakeWikiAgent


def _seed_object(substrate):
    substrate.save_sprint(Sprint(id="s0", status=SprintStatus.DONE, goals="g", program="p1"))
    substrate.save_result(Result(id="r0", sprint="s0", summary="s", completed_at=1.0))


def test_run_wiki_launches_an_ingest_through_the_beat(wiki_bundle):
    substrate, _ = wiki_bundle
    _seed_object(substrate)
    agent = FakeWikiAgent()
    out = Service(substrate.repo_root).run_wiki("p1", "ingest", agent=agent)
    assert "launched" in out["line"]
    assert len(agent.launches) == 1
    assert wiki_store.load_state(substrate, "p1")["run"] is not None


def test_run_wiki_never_starts_a_second_run(wiki_bundle):
    substrate, _ = wiki_bundle
    _seed_object(substrate)
    svc, agent = Service(substrate.repo_root), FakeWikiAgent()
    svc.run_wiki("p1", "ingest", agent=agent)
    out = svc.run_wiki("p1", "ingest", agent=agent)
    assert out["line"] == "wiki: running"
    assert len(agent.launches) == 1


def test_run_wiki_rejects_an_unknown_kind(wiki_bundle):
    substrate, _ = wiki_bundle
    with pytest.raises(ValueError):
        Service(substrate.repo_root).run_wiki("p1", "reticulate", agent=FakeWikiAgent())


def test_unquarantine_clears_the_list_and_says_what_it_cleared(wiki_bundle):
    substrate, _ = wiki_bundle
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = ["result:r9", "result:r8"]
    out = Service(substrate.repo_root).unquarantine_wiki("p1")
    assert sorted(out["cleared"]) == ["result:r8", "result:r9"]
    assert wiki_store.load_state(substrate, "p1")["quarantined"] == []


def test_unquarantine_with_nothing_quarantined_is_a_no_op(wiki_bundle):
    substrate, _ = wiki_bundle
    assert Service(substrate.repo_root).unquarantine_wiki("p1")["cleared"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_runs.py -v`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'run_wiki'`

- [ ] **Step 3: Write minimal implementation**

Append inside `class Service`:

```python
    def run_wiki(self, program_id: str, kind: str = "ingest", agent=None) -> dict:
        """Force one wiki beat now.

        The usage gate is forced open: a human pressing the button is a stronger
        signal than the gate, which exists to stop *unattended* loops burning a
        window. `kind="lint"` pushes ingests_since_lint to the threshold and lets
        beat() decide, rather than adding a second definition of what a run is."""
        from coscience import wiki, wiki_store
        if kind not in ("ingest", "lint"):
            raise ValueError(f"kind must be ingest or lint: {kind}")
        program = self.substrate.load_program(program_id)
        if kind == "lint":
            with wiki_store.state_guard(self.substrate, program_id) as state:
                state["ingests_since_lint"] = max(int(state.get("ingests_since_lint", 0)),
                                                  wiki.lint_every())
        real = agent if agent is not None else self._wiki_agent()
        line = wiki.beat(self.substrate, program, time.time(), real,
                         usage_gate=lambda: True)
        return {"line": line or "wiki: nothing to do"}

    def _wiki_agent(self):
        from coscience import wiki_agent
        return wiki_agent.WikiAgent()

    def unquarantine_wiki(self, program_id: str) -> dict:
        """Clear the quarantine list so the objects become pending again. Their
        ingested entries are untouched — pending_objects re-derives from hashes."""
        from coscience import wiki_store
        with wiki_store.state_guard(self.substrate, program_id) as state:
            cleared = list(state.get("quarantined") or [])
            state["quarantined"] = []
        if cleared:
            self.substrate.commit(f"wiki {program_id}: unquarantined {len(cleared)}")
        return {"cleared": cleared}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_runs.py -v`
Expected: PASS (5 tests)

`wiki.lint_every()` (`wiki.py:43`, default 5 via `COSCIENCE_WIKI_LINT_EVERY`) and `substrate.load_program` (`substrate.py:291`) are both verified names.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_runs.py
git commit -m "feat(wiki): Service run controls — forced beat and unquarantine"
```

---

### Task 8: HTTP read endpoints

**Files:**
- Modify: `src/coscience/http_api.py` (beside the artifact routes, ~line 640)
- Test: `tests/test_http_wiki_read.py`

**Interfaces:**
- Consumes: every `Service` read method from Task 4.
- Produces: `GET /api/programs/{program_id}/wiki`, `/wiki/pages`, `/wiki/pages/{slug:path}`, `/wiki/search?q=&limit=`, `/wiki/log`, `/wiki/lint`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_wiki_read.py
from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_store
from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/lease.md", type="Concept", title="Compute lease",
        body="# Definition\n\n" + "x" * 400))


def test_summary_lists_counts(substrate):
    _seed(substrate)
    r = _client(substrate).get("/api/programs/p1/wiki")
    assert r.status_code == 200
    assert r.json()["counts"] == {"Concept": 1}


def test_pages_and_page_detail(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert [p["path"] for p in c.get("/api/programs/p1/wiki/pages").json()] == \
        ["concepts/lease.md"]
    r = c.get("/api/programs/p1/wiki/pages/concepts/lease")
    assert r.status_code == 200
    assert r.json()["title"] == "Compute lease"


def test_a_nested_slug_survives_the_path_converter(substrate):
    # Without {slug:path} the slash in "concepts/lease" never matches the route.
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/pages/concepts/lease").status_code == 200


def test_a_missing_page_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/pages/concepts/nope").status_code == 404


def test_a_traversing_slug_is_404_not_a_file_read(substrate):
    _seed(substrate)
    r = _client(substrate).get("/api/programs/p1/wiki/pages/../../../etc/passwd")
    assert r.status_code == 404
    assert "root:" not in r.text


def test_search_log_and_lint(substrate):
    _seed(substrate)
    c = _client(substrate)
    hits = c.get("/api/programs/p1/wiki/search", params={"q": "compute"}).json()
    assert [h["path"] for h in hits] == ["concepts/lease.md"]
    assert c.get("/api/programs/p1/wiki/log").status_code == 200
    assert "counts" in c.get("/api/programs/p1/wiki/lint").json()


def test_a_blank_search_returns_an_empty_list(substrate):
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/search", params={"q": ""}).json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_read.py -v`
Expected: FAIL — 404s on every route

- [ ] **Step 3: Write minimal implementation**

Add to `http_api.py`, after the artifact-tags route:

```python
    # --- wiki (phase 2) ------------------------------------------------------
    # {slug:path}: a page's address is its bundle path without .md
    # ("concepts/auth-gate"), because a bare filename stem collides across type
    # directories. Service.wiki_page_path is what makes the converter safe.

    @api.get("/programs/{program_id}/wiki")
    def wiki_summary(program_id: str) -> dict:
        return service.wiki_summary(program_id)

    @api.get("/programs/{program_id}/wiki/pages")
    def list_wiki_pages(program_id: str) -> list[dict]:
        return service.list_wiki_pages(program_id)

    @api.get("/programs/{program_id}/wiki/search")
    def search_wiki(program_id: str, q: str = "", limit: int = 50) -> list[dict]:
        return service.search_wiki(program_id, q, limit=limit)

    @api.get("/programs/{program_id}/wiki/log")
    def wiki_log(program_id: str) -> dict:
        return {"text": service.wiki_log(program_id)}

    @api.get("/programs/{program_id}/wiki/lint")
    def wiki_lint_report(program_id: str) -> dict:
        return service.wiki_lint_report(program_id)

    # Registered last of the GETs: a literal path like /wiki/pages must not be
    # swallowed by the {slug:path} pattern.
    @api.get("/programs/{program_id}/wiki/pages/{slug:path}")
    def get_wiki_page(program_id: str, slug: str) -> dict:
        try:
            return service.get_wiki_page(program_id, slug)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"page not found: {slug}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_read.py -v`
Expected: PASS (7 tests)

If `/wiki/pages` returns a page-detail 404 instead of the list, the `{slug:path}` route is registered before it — move it below.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/http_api.py tests/test_http_wiki_read.py
git commit -m "feat(wiki): HTTP read endpoints for summary, pages, search, log, lint"
```

---

### Task 9: HTTP curation and run endpoints

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_wiki_write.py`

**Interfaces:**
- Consumes: Tasks 5–7's `Service` methods; `current_user` / `auth.User` as used by `http_api.py:597-611`.
- Produces: `POST /wiki/run`, `POST /wiki/unquarantine`, `POST /wiki/pages/{slug:path}/verify`, `POST /wiki/status/{slug:path}`, `POST /wiki/notes/{slug:path}`, `DELETE /wiki/pages/{slug:path}`.
- Request bodies: `WikiRunIn {kind: str = "ingest"}`, `WikiStatusIn {status: str}`, `WikiNotesIn {text: str}`.

The `verify` route builds the actor string `human:<username>` server-side. A client must never be able to claim it was someone else, and an unauthenticated call must not forge a human review.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_wiki_write.py
from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_store
from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A",
        body="# Definition\n\n" + "x" * 400))


def test_verify_marks_the_page_and_derives_the_tier(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/pages/concepts/a/verify")
    assert r.status_code == 200
    assert r.json()["trust"] == "human-reviewed"


def test_the_verify_actor_is_built_server_side_and_is_always_a_human_prefix(substrate):
    _seed(substrate)
    body = _client(substrate).post(
        "/api/programs/p1/wiki/pages/concepts/a/verify").json()
    assert body["verified"][-1]["by"].startswith("human:")


def test_status_accepts_a_lifecycle_value_and_rejects_others(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p1/wiki/status/concepts/a",
                  json={"status": "stable"}).json()["status"] == "stable"
    assert c.post("/api/programs/p1/wiki/status/concepts/a",
                  json={"status": "verified"}).status_code == 400


def test_notes_write_the_protected_section(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/notes/concepts/a",
                                json={"text": "mind the lease id"})
    assert r.json()["human_notes"] == "mind the lease id"


def test_delete_removes_the_page(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.delete("/api/programs/p1/wiki/pages/concepts/a").status_code == 200
    assert c.get("/api/programs/p1/wiki/pages/concepts/a").status_code == 404


def test_unquarantine_reports_what_it_cleared(substrate):
    _seed(substrate)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = ["result:r9"]
    r = _client(substrate).post("/api/programs/p1/wiki/unquarantine")
    assert r.json()["cleared"] == ["result:r9"]


def test_run_rejects_an_unknown_kind_with_400(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/run", json={"kind": "nope"})
    assert r.status_code == 400


def test_curating_a_missing_page_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).post(
        "/api/programs/p1/wiki/pages/concepts/ghost/verify").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_write.py -v`
Expected: FAIL — 404/405 on every route

- [ ] **Step 3: Write minimal implementation**

Add the models beside the other `*In` models in `http_api.py`:

```python
class WikiRunIn(BaseModel):
    kind: str = "ingest"


class WikiStatusIn(BaseModel):
    status: str


class WikiNotesIn(BaseModel):
    text: str
```

And the routes, after Task 8's block:

```python
    @api.post("/programs/{program_id}/wiki/run")
    def run_wiki(program_id: str, body: WikiRunIn) -> dict:
        try:
            return service.run_wiki(program_id, body.kind)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @api.post("/programs/{program_id}/wiki/unquarantine")
    def unquarantine_wiki(program_id: str) -> dict:
        return service.unquarantine_wiki(program_id)

    @api.post("/programs/{program_id}/wiki/pages/{slug:path}/verify")
    def verify_wiki_page(program_id: str, slug: str,
                         user: "auth.User | None" = Depends(current_user)) -> dict:
        # The actor is built here, never accepted from the client: a request must
        # not be able to claim a human review was done by someone else.
        actor = f"human:{user.username}" if user else "human:anonymous"
        try:
            return service.verify_wiki_page(program_id, slug, by=actor)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"page not found: {slug}")

    @api.post("/programs/{program_id}/wiki/status/{slug:path}")
    def set_wiki_page_status(program_id: str, slug: str, body: WikiStatusIn) -> dict:
        try:
            return service.set_wiki_page_status(program_id, slug, body.status)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"page not found: {slug}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @api.post("/programs/{program_id}/wiki/notes/{slug:path}")
    def set_wiki_human_notes(program_id: str, slug: str, body: WikiNotesIn) -> dict:
        try:
            return service.set_wiki_human_notes(program_id, slug, body.text)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"page not found: {slug}")

    @api.delete("/programs/{program_id}/wiki/pages/{slug:path}")
    def delete_wiki_page(program_id: str, slug: str) -> dict:
        try:
            return service.delete_wiki_page(program_id, slug)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"page not found: {slug}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_write.py -v`
Expected: PASS (8 tests)

Note `test_run_rejects_an_unknown_kind_with_400` never reaches an agent, since the kind check precedes the launch. No test in this task may start a process.

- [ ] **Step 5: Run the whole backend suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS, count = previous total + this plan's new tests.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/http_api.py tests/test_http_wiki_write.py
git commit -m "feat(wiki): HTTP curation and run endpoints"
```

---

### Task 10: `api.ts` — wiki types and client methods

**Files:**
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts` (extend)

**Interfaces:**
- Produces the types `WikiSummary`, `WikiPageRow`, `WikiPage`, `WikiRelation`, `WikiBacklink`, `WikiSource`, `WikiHit`, `WikiLintReport`, `WikiTrust`, and the `api.*` methods `getWikiSummary`, `listWikiPages`, `getWikiPage`, `searchWiki`, `getWikiLog`, `getWikiLint`, `runWiki`, `unquarantineWiki`, `verifyWikiPage`, `setWikiPageStatus`, `setWikiHumanNotes`, `deleteWikiPage`.

Field names must match Tasks 1–7's payloads exactly. A slug goes into the URL as a path, so each segment is encoded separately — `encodeURIComponent` on the whole thing would turn the `/` into `%2F` and break the route.

- [ ] **Step 1: Write the failing test**

```typescript
// append to frontend/src/api.test.ts
import { describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("wiki api", () => {
  const ok = (body: unknown) => {
    const spy = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: () => Promise.resolve(body),
    });
    vi.stubGlobal("fetch", spy);
    return spy;
  };

  it("fetches a summary", async () => {
    const spy = ok({ counts: { Concept: 1 } });
    const out = await api.getWikiSummary("p1");
    expect(spy).toHaveBeenCalledWith("/api/programs/p1/wiki");
    expect(out.counts.Concept).toBe(1);
  });

  it("keeps the slash in a page slug so the path route matches", async () => {
    const spy = ok({ path: "concepts/a.md" });
    await api.getWikiPage("p1", "concepts/a");
    expect(spy).toHaveBeenCalledWith("/api/programs/p1/wiki/pages/concepts/a");
  });

  it("encodes each slug segment without eating the separator", async () => {
    const spy = ok({});
    await api.getWikiPage("p1", "concepts/a b");
    expect(spy).toHaveBeenCalledWith("/api/programs/p1/wiki/pages/concepts/a%20b");
  });

  it("posts a status change", async () => {
    const spy = ok({ status: "stable" });
    await api.setWikiPageStatus("p1", "concepts/a", "stable");
    expect(spy.mock.calls[0][0]).toBe("/api/programs/p1/wiki/status/concepts/a");
    expect(spy.mock.calls[0][1]).toMatchObject({ method: "POST" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: FAIL — `api.getWikiSummary is not a function`

- [ ] **Step 3: Write minimal implementation**

Add to `frontend/src/api.ts`:

```typescript
export type WikiTrust = "unverified" | "machine-confirmed" | "human-reviewed";
export interface WikiSummary {
  counts: Record<string, number>;
  trust: Record<WikiTrust, number>;
  pages: number; pending: number; quarantined: string[];
  run: { id: string; kind: string } | null;
  last_run: { id: string; kind: string; status: string; at: number;
              pages_created: number; pages_updated: number; notes: string;
              escaped: string[] } | null;
  ingests_since_lint: number;
  lint: Record<string, number>;
  index_md: string;
}
export interface WikiPageRow {
  path: string; slug: string; type: string; title: string;
  status: string; trust: WikiTrust; stale_after: string; tags: string[];
}
export interface WikiRelation {
  type: string; target: string; title: string; exists: boolean;
  confidence: string; source: string;
}
export interface WikiBacklink { path: string; title: string; type: string; typed: string[] }
export interface WikiSource {
  id: string; kind: "result" | "sprint" | "artifact" | "unknown";
  href: string; resource: string; title: string;
}
export interface WikiPage extends WikiPageRow {
  description: string; aliases: string[]; body: string; human_notes: string;
  verified: { by: string; at: number }[];
  relations: WikiRelation[]; backlinks: WikiBacklink[]; sources: WikiSource[];
}
export interface WikiHit {
  path: string; title: string; type: string; trust: WikiTrust;
  score: number; excerpt: string;
}
export interface WikiLintFinding {
  rule: string; severity: string; path: string; message: string;
}
export interface WikiLintReport {
  counts: Record<string, number>; findings: WikiLintFinding[];
}

/** A page address is a path ("concepts/auth-gate"), so each segment is encoded
 *  on its own — encodeURIComponent on the whole slug would turn the separator
 *  into %2F and the route would never match. */
const slugPath = (slug: string) =>
  slug.split("/").map(encodeURIComponent).join("/");
```

And inside the exported `api` object:

```typescript
  getWikiSummary: (id: string) =>
    fetch(`/api/programs/${id}/wiki`).then(j<WikiSummary>),
  listWikiPages: (id: string) =>
    fetch(`/api/programs/${id}/wiki/pages`).then(j<WikiPageRow[]>),
  getWikiPage: (id: string, slug: string) =>
    fetch(`/api/programs/${id}/wiki/pages/${slugPath(slug)}`).then(j<WikiPage>),
  searchWiki: (id: string, q: string) =>
    fetch(`/api/programs/${id}/wiki/search?q=${encodeURIComponent(q)}`).then(j<WikiHit[]>),
  getWikiLog: (id: string) =>
    fetch(`/api/programs/${id}/wiki/log`).then(j<{ text: string }>),
  getWikiLint: (id: string) =>
    fetch(`/api/programs/${id}/wiki/lint`).then(j<WikiLintReport>),
  runWiki: (id: string, kind: "ingest" | "lint") =>
    fetch(`/api/programs/${id}/wiki/run`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }).then(j<{ line: string }>),
  unquarantineWiki: (id: string) =>
    fetch(`/api/programs/${id}/wiki/unquarantine`, { method: "POST" })
      .then(j<{ cleared: string[] }>),
  verifyWikiPage: (id: string, slug: string) =>
    fetch(`/api/programs/${id}/wiki/pages/${slugPath(slug)}/verify`, { method: "POST" })
      .then(j<WikiPage>),
  setWikiPageStatus: (id: string, slug: string, status: string) =>
    fetch(`/api/programs/${id}/wiki/status/${slugPath(slug)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then(j<WikiPage>),
  setWikiHumanNotes: (id: string, slug: string, text: string) =>
    fetch(`/api/programs/${id}/wiki/notes/${slugPath(slug)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<WikiPage>),
  deleteWikiPage: (id: string, slug: string) =>
    fetch(`/api/programs/${id}/wiki/pages/${slugPath(slug)}`, { method: "DELETE" })
      .then(j<{ deleted: string; relations_dropped: unknown[] }>),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: PASS

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(wiki): api.ts wiki types and client methods"
```

---

### Task 11: `wikiPage.ts` — link rewriting and outline

**Files:**
- Create: `frontend/src/components/wikiPage.ts`
- Test: `frontend/src/components/wikiPage.test.ts`

**Interfaces:**
- Produces:
  - `wikiHref(programId: string, target: string) -> string` — a bundle link as a dashboard route
  - `isInternalLink(href: string) -> boolean`
  - `outline(body: string) -> { level: number; text: string; id: string }[]`
  - `headingId(text: string) -> string`

This is the module §12 already specifies, and it is why Task 12 does not need `PageToc.tsx` (D4).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/wikiPage.test.ts
import { describe, expect, it } from "vitest";
import { headingId, isInternalLink, outline, wikiHref } from "./wikiPage";

describe("wikiHref", () => {
  it("routes a bundle-absolute page link into the wiki view", () => {
    expect(wikiHref("p1", "/concepts/auth-gate.md"))
      .toBe("/programs/p1/wiki/concepts/auth-gate");
  });

  it("routes a relative link the same way", () => {
    expect(wikiHref("p1", "concepts/auth-gate.md"))
      .toBe("/programs/p1/wiki/concepts/auth-gate");
  });

  it("drops a trailing anchor from the page part but keeps it on the route", () => {
    expect(wikiHref("p1", "/concepts/a.md#evidence"))
      .toBe("/programs/p1/wiki/concepts/a#evidence");
  });

  it("leaves an external link alone", () => {
    expect(wikiHref("p1", "https://example.org/x")).toBe("https://example.org/x");
  });
});

describe("isInternalLink", () => {
  it("is true for a bundle page and false for anything with a scheme", () => {
    expect(isInternalLink("/concepts/a.md")).toBe(true);
    expect(isInternalLink("concepts/a.md")).toBe(true);
    expect(isInternalLink("https://example.org")).toBe(false);
    expect(isInternalLink("mailto:a@b.c")).toBe(false);
  });
});

describe("outline", () => {
  it("lists headings with their level and a stable anchor id", () => {
    const items = outline("# Definition\n\ntext\n\n## Detail\n\n# Evidence\n");
    expect(items).toEqual([
      { level: 1, text: "Definition", id: "definition" },
      { level: 2, text: "Detail", id: "detail" },
      { level: 1, text: "Evidence", id: "evidence" },
    ]);
  });

  it("ignores a heading inside a fenced code block", () => {
    expect(outline("# Real\n\n```\n# Not a heading\n```\n")).toEqual([
      { level: 1, text: "Real", id: "real" },
    ]);
  });

  it("disambiguates two headings with the same text", () => {
    const items = outline("# Notes\n\n# Notes\n");
    expect(items.map((i) => i.id)).toEqual(["notes", "notes-2"]);
  });

  it("returns nothing for a body with no headings", () => {
    expect(outline("just prose")).toEqual([]);
  });
});

describe("headingId", () => {
  it("slugifies punctuation and spacing", () => {
    expect(headingId("Auth gate (401 on forged session)"))
      .toBe("auth-gate-401-on-forged-session");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/wikiPage.test.ts`
Expected: FAIL — cannot resolve `./wikiPage`

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/components/wikiPage.ts
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/wikiPage.test.ts`
Expected: PASS (10 assertions across 4 describes)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add frontend/src/components/wikiPage.ts frontend/src/components/wikiPage.test.ts
git commit -m "feat(wiki): wikiPage.ts — internal link routing and heading outline"
```

---

### Task 12: `WikiView` — header and page tree

**Files:**
- Create: `frontend/src/views/WikiView.tsx`
- Modify: `frontend/src/styles.css` (append a `.wiki-*` section)
- Test: `frontend/src/views/WikiView.test.tsx`

**Interfaces:**
- Consumes: `api.getWikiSummary`, `api.listWikiPages`, `api.searchWiki`, `api.runWiki`, `api.unquarantineWiki` (Task 10); `useQuery` / `useMutation` as used in `ArtifactsView.tsx:67-74`.
- Produces: the default-exported `WikiView` component, reading `:id` and the optional page path from the router.

Header: counts by type, trust breakdown, pending count, last run and outcome, lint badge, "Ingest now" / "Lint now", and a quarantine warning with a retry button. Left: search box and a tree grouped by type, each row carrying a trust dot and a stale marker.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/views/WikiView.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WikiView from "./WikiView";
import { api } from "../api";

const summary = {
  counts: { Concept: 2, Entity: 1 },
  trust: { unverified: 2, "machine-confirmed": 0, "human-reviewed": 1 },
  pages: 3, pending: 4, quarantined: [] as string[], run: null,
  last_run: { id: "r0001", kind: "ingest", status: "ok", at: 1, pages_created: 18,
              pages_updated: 3, notes: "", escaped: [] },
  ingests_since_lint: 1, lint: { error: 0, warn: 2 }, index_md: "# Index",
};
const rows = [
  { path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha", status: "stable",
    trust: "human-reviewed", stale_after: "", tags: [] },
  { path: "concepts/b.md", slug: "b", type: "Concept", title: "Beta", status: "draft",
    trust: "unverified", stale_after: "2020-01-01", tags: [] },
  { path: "entities/c.md", slug: "c", type: "Entity", title: "Gamma", status: "",
    trust: "unverified", stale_after: "", tags: [] },
];

// Mantine reads matchMedia and ResizeObserver; jsdom has neither. Same stubs as
// ProgramDetail.test.tsx:10-18 — without them the provider throws on mount.
beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as never;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as never);
});

function mount(path = "/programs/p1/wiki") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/programs/:id/wiki" element={<WikiView />} />
          <Route path="/programs/:id/wiki/*" element={<WikiView />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>,
  );
}

describe("WikiView header and tree", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiPage").mockResolvedValue({
      path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha",
      status: "stable", trust: "human-reviewed", stale_after: "", tags: [],
      description: "", aliases: [], body: "# Definition\n\nbody text\n",
      human_notes: "", verified: [], relations: [], backlinks: [], sources: [],
    } as never);
  });

  it("shows counts, pending and the last run outcome", async () => {
    mount();
    expect(await screen.findByText(/4 pending/i)).toBeTruthy();
    expect(screen.getByText(/Concept/)).toBeTruthy();
    expect(screen.getByText(/ingest ok/i)).toBeTruthy();
  });

  it("groups the tree by page type", async () => {
    mount();
    expect(await screen.findByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Gamma")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Entities/i })).toBeTruthy();
  });

  it("marks a stale page", async () => {
    mount();
    await screen.findByText("Beta");
    expect(screen.getByTitle(/stale/i)).toBeTruthy();
  });

  it("triggers an ingest run from the header button", async () => {
    const run = vi.spyOn(api, "runWiki").mockResolvedValue({ line: "ok" } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /ingest now/i }));
    await waitFor(() => expect(run).toHaveBeenCalledWith("p1", "ingest"));
  });

  it("offers a retry only when something is quarantined", async () => {
    mount();
    await screen.findByText("Alpha");
    expect(screen.queryByRole("button", { name: /retry quarantined/i })).toBeNull();

    vi.spyOn(api, "getWikiSummary")
      .mockResolvedValue({ ...summary, quarantined: ["result:r9"] } as never);
    mount();
    expect(await screen.findByRole("button", { name: /retry quarantined/i })).toBeTruthy();
  });

  it("searches when the box has a query", async () => {
    const search = vi.spyOn(api, "searchWiki").mockResolvedValue(
      [{ path: "concepts/a.md", title: "Alpha", type: "Concept",
         trust: "unverified", score: 3, excerpt: "…lease…" }] as never);
    mount();
    fireEvent.change(await screen.findByPlaceholderText(/search/i),
                     { target: { value: "lease" } });
    await waitFor(() => expect(search).toHaveBeenCalledWith("p1", "lease"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx`
Expected: FAIL — cannot resolve `./WikiView`

- [ ] **Step 3: Write minimal implementation**

First append to `frontend/src/styles.css`, following the `/* ── name ─── */` section idiom the file already uses (see the `.page-toc` and `.sprint-unseen` sections). Use the file's existing custom properties — `--hairline`, `--ink-faint`, `--ink-muted`, `--paper-2`, `--machine`, `--machine-weak`, `--signal`, `--signal-weak` — never raw hex; grep the top of `styles.css` for the full set before adding a colour.

```css
/* ── wiki browse: three panes, trust dots, section chips ────────── */
.wiki-panes { display: grid; grid-template-columns: 240px minmax(0, 1fr) 260px; gap: 16px; }
@media (max-width: 1100px) { .wiki-panes { grid-template-columns: 1fr; } }

.wiki-tree h4, .wiki-side h4 {
  font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--ink-faint); margin: 12px 0 4px; font-weight: 500;
}
.wiki-tree ul, .wiki-side ul { list-style: none; margin: 0; padding: 0; }
.wiki-tree li, .wiki-side li {
  display: flex; align-items: center; gap: 6px; padding: 1px 0; font-size: 13px;
}
.wiki-excerpt { font-size: 12px; color: var(--ink-faint); }

/* Trust is derived, never stored, so this dot is the only place a page's tier is
   visible at a glance. Unverified is deliberately hollow rather than grey: absent
   evidence should read as absent, not as a dimmer kind of confirmation. */
.wiki-dot {
  width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto;
  border: 1px solid var(--hairline);
}
.wiki-dot--unverified { background: transparent; }
.wiki-dot--machine-confirmed { background: var(--ink-faint); }
.wiki-dot--human-reviewed { background: var(--machine); border-color: var(--machine); }
.wiki-stale { color: var(--signal); cursor: help; }

/* min-width: 0 so a long code block inside the page scrolls instead of forcing
   the whole grid wider than the viewport. */
.wiki-page { min-width: 0; }
.wiki-page pre, .wiki-page table { overflow-x: auto; max-width: 100%; }
```

Then `WikiView.tsx`. Built from Mantine like every other view, reusing `cardStyle`, the `.eyebrow` label idiom and `<Loader color="machine" />`:

```tsx
import { Badge, Button, Card, Group, Loader, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type WikiPageRow } from "../api";
import { BackLink, EmptyState } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

const TYPE_ORDER = ["Concept", "Entity", "Synthesis", "Source", "Question"];
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
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [q, setQ] = useState("");

  const summary = useQuery({ queryKey: ["wiki", id],
                             queryFn: () => api.getWikiSummary(id) });
  const pages = useQuery({ queryKey: ["wiki-pages", id],
                           queryFn: () => api.listWikiPages(id) });
  const hits = useQuery({ queryKey: ["wiki-search", id, q], enabled: q.trim().length > 0,
                          queryFn: () => api.searchWiki(id, q) });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["wiki", id] });
    qc.invalidateQueries({ queryKey: ["wiki-pages", id] });
  };
  const run = useMutation({ mutationFn: (kind: "ingest" | "lint") => api.runWiki(id, kind),
                            onSuccess: invalidate });
  const unquarantine = useMutation({ mutationFn: () => api.unquarantineWiki(id),
                                     onSuccess: invalidate });

  const s = summary.data;
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
          <Group gap={8}>
            <Button size="xs" variant="default" onClick={() => run.mutate("ingest")}
                    disabled={!!s?.run}>Ingest now</Button>
            <Button size="xs" variant="default" onClick={() => run.mutate("lint")}
                    disabled={!!s?.run}>Lint now</Button>
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
          {s.run && <Badge variant="light" color="machine">running…</Badge>}
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
              .map(([type, rows]) => (
                <div key={type}>
                  <h4>{GROUP_LABEL[type] ?? type}</h4>
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
                </div>
              ))
          )}
        </nav>
        <main className="wiki-page" />
        <aside className="wiki-side" />
      </div>
    </Stack>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiView.tsx frontend/src/views/WikiView.test.tsx frontend/src/styles.css
git commit -m "feat(wiki): WikiView header and type-grouped page tree"
```

---

### Task 13: `WikiView` centre pane — rendered page and provenance chips

**Files:**
- Modify: `frontend/src/views/WikiView.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/views/WikiView.test.tsx` (extend)

**Interfaces:**
- Consumes: `api.getWikiPage`; `wikiHref` / `isInternalLink` (Task 11); `Md` from `../components/Md`.

Internal markdown links are intercepted and routed client-side rather than navigating away. `sources` render as provenance chips linking to `/results/:id`, `/sprints/:id` and `/programs/:id/artifacts/:aid` — this is the payoff for building the wiki inside the platform.

- [ ] **Step 1: Write the failing test**

```tsx
// append to frontend/src/views/WikiView.test.tsx
describe("WikiView centre pane", () => {
  const page = {
    path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha",
    status: "stable", trust: "human-reviewed", stale_after: "", tags: ["auth"],
    description: "d", aliases: [], human_notes: "", verified: [],
    body: "# Definition\n\nSee [Beta](/concepts/b.md) and [out](https://example.org).\n",
    relations: [], backlinks: [],
    sources: [
      { id: "c1", kind: "result", href: "/results/r7", resource: "/results/r7.md",
        title: "R7" },
      { id: "c2", kind: "artifact", href: "/programs/p1/artifacts/fig",
        resource: "/programs/p1/artifacts/fig/v1", title: "fig" },
      { id: "c3", kind: "unknown", href: "", resource: "https://x.test", title: "x" },
    ],
  };

  beforeEach(() => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiPage").mockResolvedValue(page as never);
  });

  it("renders the page title and body", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect(await screen.findByRole("heading", { name: "Alpha" })).toBeTruthy();
    // By role, not by text: the right pane's outline also renders the word
    // "Definition" as a link, so a bare text query matches two elements.
    expect(screen.getByRole("heading", { name: "Definition" })).toBeTruthy();
  });

  it("rewrites an internal body link to a client-side wiki route", async () => {
    mount("/programs/p1/wiki/concepts/a");
    const link = await screen.findByRole("link", { name: "Beta" });
    expect(link.getAttribute("href")).toBe("/programs/p1/wiki/concepts/b");
  });

  it("leaves an external body link pointing out", async () => {
    mount("/programs/p1/wiki/concepts/a");
    const link = await screen.findByRole("link", { name: "out" });
    expect(link.getAttribute("href")).toBe("https://example.org");
  });

  it("renders provenance chips that link back to the platform", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect((await screen.findByRole("link", { name: /R7/ })).getAttribute("href"))
      .toBe("/results/r7");
    expect(screen.getByRole("link", { name: /fig/ }).getAttribute("href"))
      .toBe("/programs/p1/artifacts/fig");
  });

  it("shows an unroutable source as text rather than a dead link", async () => {
    mount("/programs/p1/wiki/concepts/a");
    await screen.findByRole("heading", { name: "Alpha" });
    expect(screen.queryByRole("link", { name: /^x$/ })).toBeNull();
    expect(screen.getByText(/x/)).toBeTruthy();
  });

  it("falls back to the index when no page is selected", async () => {
    mount("/programs/p1/wiki");
    expect(await screen.findByText(/Index/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx`
Expected: FAIL — the new describe block fails; the centre pane is still empty

- [ ] **Step 3: Write minimal implementation**

In `WikiView.tsx`, read the selected page from the splat, add the query, and fill `<main>`:

```tsx
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { isInternalLink, wikiHref } from "../components/wikiPage";
// ...
  const { id = "", "*": splat = "" } = useParams();
  const slug = splat.replace(/\.md$/, "");

  const page = useQuery({ queryKey: ["wiki-page", id, slug], enabled: !!slug,
                          queryFn: () => api.getWikiPage(id, slug) });
```

and the centre pane:

```tsx
        <main className="wiki-page">
          {!slug && s && (
            <Card padding="lg" radius="md" style={cardStyle}>
              <div className="eyebrow" style={{ marginBottom: 12 }}>index</div>
              <div className="report-leaf"><Md>{s.index_md}</Md></div>
            </Card>
          )}
          {slug && page.data && (
            <Card padding="lg" radius="md" style={cardStyle}>
              <Text component="h2" fw={600} size="lg" mb={6}>{page.data.title}</Text>
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

              <div className="report-leaf">
                <Md components={{
                  // An internal link must route inside the app; letting the browser
                  // follow /concepts/b.md would leave the dashboard entirely.
                  a: ({ href, children, ...rest }) => (
                    isInternalLink(String(href ?? ""))
                      ? <Link to={wikiHref(id, String(href))}>{children}</Link>
                      : <a href={String(href ?? "")} target="_blank"
                           rel="noreferrer" {...rest}>{children}</a>
                  ),
                }}>{page.data.body}</Md>
              </div>
            </Card>
          )}
        </main>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx`
Expected: PASS (12 tests total)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiView.tsx frontend/src/views/WikiView.test.tsx frontend/src/styles.css
git commit -m "feat(wiki): WikiView centre pane — routed links and provenance chips"
```

---

### Task 14: `WikiView` right pane — outline, backlinks, relations, trust panel

**Files:**
- Modify: `frontend/src/views/WikiView.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/views/WikiView.test.tsx` (extend)

**Interfaces:**
- Consumes: `outline` (Task 11); `api.verifyWikiPage`, `api.setWikiPageStatus`, `api.setWikiHumanNotes` (Task 10).

Right pane: outline, backlinks, typed relations grouped by type, and a trust panel — "Mark verified", a status select, and the `# Human notes` editor. This is the half of phase 2 that makes the milestone "read **and curate**".

- [ ] **Step 1: Write the failing test**

```tsx
// append to frontend/src/views/WikiView.test.tsx
describe("WikiView right pane", () => {
  const page = {
    path: "concepts/a.md", slug: "a", type: "Concept", title: "Alpha",
    status: "draft", trust: "unverified", stale_after: "", tags: [],
    description: "", aliases: [], verified: [], human_notes: "old note",
    body: "# Definition\n\nd\n\n# Evidence\n\ne\n",
    relations: [
      { type: "part_of", target: "concepts/b.md", title: "Beta", exists: true,
        confidence: "high", source: "c1" },
      { type: "requires", target: "concepts/gone.md", title: "", exists: false,
        confidence: "", source: "c1" },
    ],
    backlinks: [{ path: "concepts/z.md", title: "Zeta", type: "Concept",
                  typed: ["refines"] }],
    sources: [],
  };

  beforeEach(() => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(summary as never);
    vi.spyOn(api, "listWikiPages").mockResolvedValue(rows as never);
    vi.spyOn(api, "getWikiPage").mockResolvedValue(page as never);
  });

  it("lists the body outline", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect(await screen.findByRole("link", { name: "Evidence" })).toBeTruthy();
  });

  it("shows backlinks with the relation types pointing here", async () => {
    mount("/programs/p1/wiki/concepts/a");
    expect(await screen.findByRole("link", { name: "Zeta" })).toBeTruthy();
    expect(screen.getByText(/refines/)).toBeTruthy();
  });

  it("marks a relation whose target does not exist", async () => {
    mount("/programs/p1/wiki/concepts/a");
    await screen.findByText(/part_of/);
    expect(screen.getByTitle(/missing/i)).toBeTruthy();
  });

  it("marks the page verified", async () => {
    const verify = vi.spyOn(api, "verifyWikiPage")
      .mockResolvedValue({ ...page, trust: "human-reviewed" } as never);
    mount("/programs/p1/wiki/concepts/a");
    fireEvent.click(await screen.findByRole("button", { name: /mark verified/i }));
    await waitFor(() => expect(verify).toHaveBeenCalledWith("p1", "concepts/a"));
  });

  it("changes the lifecycle status", async () => {
    const setStatus = vi.spyOn(api, "setWikiPageStatus")
      .mockResolvedValue({ ...page, status: "stable" } as never);
    mount("/programs/p1/wiki/concepts/a");
    fireEvent.change(await screen.findByLabelText(/status/i),
                     { target: { value: "stable" } });
    await waitFor(() =>
      expect(setStatus).toHaveBeenCalledWith("p1", "concepts/a", "stable"));
  });

  it("saves the human notes", async () => {
    const save = vi.spyOn(api, "setWikiHumanNotes")
      .mockResolvedValue({ ...page, human_notes: "new note" } as never);
    mount("/programs/p1/wiki/concepts/a");
    const box = await screen.findByLabelText(/human notes/i);
    fireEvent.change(box, { target: { value: "new note" } });
    fireEvent.click(screen.getByRole("button", { name: /save notes/i }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("p1", "concepts/a", "new note"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx`
Expected: FAIL — the right pane is still empty

- [ ] **Step 3: Write minimal implementation**

Add to `WikiView.tsx` — the mutations, a notes draft, and the `<aside>`:

```tsx
import { outline } from "../components/wikiPage";
// ...
  const [notes, setNotes] = useState<string | null>(null);
  const refreshPage = () => {
    qc.invalidateQueries({ queryKey: ["wiki-page", id, slug] });
    invalidate();
  };
  const verify = useMutation({ mutationFn: () => api.verifyWikiPage(id, slug),
                               onSuccess: refreshPage });
  const setStatus = useMutation({
    mutationFn: (status: string) => api.setWikiPageStatus(id, slug, status),
    onSuccess: refreshPage });
  const saveNotes = useMutation({
    mutationFn: (text: string) => api.setWikiHumanNotes(id, slug, text),
    onSuccess: () => { setNotes(null); refreshPage(); } });
```

```tsx
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
```

Add `Textarea` to the Mantine import from Task 12. The `<label htmlFor="wiki-notes">` above pairs with the `id`, which is what makes `getByLabelText(/human notes/i)` find the field — Mantine's own `label` prop would work too, but the explicit pairing keeps the `.eyebrow` styling consistent with the status label beside it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx`
Expected: PASS (18 tests total)

- [ ] **Step 5: Run the whole frontend suite and typecheck**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS, no type errors

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiView.tsx frontend/src/views/WikiView.test.tsx frontend/src/styles.css
git commit -m "feat(wiki): WikiView right pane — outline, relations, backlinks, curation"
```

---

### Task 15: Routes, and the `ProgramDetail` link

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/views/ProgramDetail.tsx`
- Test: `frontend/src/views/ProgramDetail.test.tsx` (extend)

**Interfaces:**
- Consumes: `WikiView` (Tasks 12–14), `api.getWikiSummary` for the badge count.

Previously blocked because `ProgramDetail.tsx` held uncommitted work; `ee062d2` committed it, so this is now an ordinary edit (see D5).

Note this task adds a **fifth** `api` call to `ProgramDetail`, which already fetches program, guidance, ideas and artifacts. Every existing test in `ProgramDetail.test.tsx` mocks exactly those four in `mockProgram`, so an unmocked fifth query would surface as a react-query error in tests that have nothing to do with the wiki. Step 2 adds the mock to the shared helper, not to one test.

- [ ] **Step 1: Add the routes to `App.tsx`**

```tsx
              <Route path="/programs/:id/wiki" element={<WikiView />} />
              <Route path="/programs/:id/wiki/*" element={<WikiView />} />
```

with `import WikiView from "./views/WikiView";` beside the other view imports. Both routes are needed: the bare path renders `index.md`, the splat renders a page.

- [ ] **Step 2: Add the wiki mock to the shared helper**

In `frontend/src/views/ProgramDetail.test.tsx`, extend `mockProgram` (the helper at the top, which already mocks `getProgram`, `listGuidance`, `listIdeas` and `listArtifacts`) with one more line, so every existing test keeps passing once the view gains a fifth query:

```tsx
  vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 0 } as any);
```

- [ ] **Step 3: Write the failing test**

The file's harness renders at `/programs/p` with program id `"p"` — not `"p1"` — via `mockProgram(instructions)` then `renderAt()`. Match it:

```tsx
// append to frontend/src/views/ProgramDetail.test.tsx
describe("wiki link", () => {
  it("links to the wiki and badges the pending count", async () => {
    mockProgram("");
    vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 4 } as any);
    renderAt();
    const link = await screen.findByRole("link", { name: /open wiki/i });
    expect(link.getAttribute("href")).toBe("/programs/p/wiki");
    // Scoped to the link: the page renders plenty of other zeros and counts, so a
    // global getByText("4") would pass or fail for unrelated reasons.
    expect(link.textContent).toMatch(/4/);
  });

  it("shows no badge when nothing is pending", async () => {
    mockProgram("");
    vi.spyOn(api, "getWikiSummary").mockResolvedValue({ pending: 0 } as any);
    renderAt();
    const link = await screen.findByRole("link", { name: /open wiki/i });
    expect(link.textContent).not.toMatch(/\d/);
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/views/ProgramDetail.test.tsx`
Expected: FAIL — no link matching `/open wiki/i`

- [ ] **Step 5: Add the query and the link**

Beside the other `useQuery` calls in `ProgramDetail`:

```tsx
  const wiki = useQuery({ queryKey: ["wiki", id], queryFn: () => api.getWikiSummary(id) });
```

and in the artifacts card's header row, matching the existing "open ideas →" / "open artifacts →" idiom:

```tsx
          <Link to={`/programs/${id}/wiki`} className="view" style={{ fontSize: 13 }}>
            open wiki →{wiki.data?.pending
              ? <Badge size="xs" variant="light" color="machine" ml={6}>
                  {wiki.data.pending}
                </Badge>
              : null}
          </Link>
```

`Badge` is already imported in this file. The count is deliberately hidden at zero — a badge reading "0" is noise, and the point of the badge is to say there is uningested work.

- [ ] **Step 6: Run the whole frontend suite**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS, no type errors. Watch for regressions in the *other* `ProgramDetail` tests: if any fail with a react-query error, Step 2's mock is missing from the shared helper.

- [ ] **Step 7: Build, to prove the bundle compiles**

Run: `cd frontend && npm run build`
Expected: success. A deploy always rebuilds (`CLAUDE.md` rule 1), so a broken build here would show as a false version-drift banner.

- [ ] **Step 8: Commit** (ask for approval first)

```bash
git add frontend/src/App.tsx frontend/src/views/ProgramDetail.tsx \
        frontend/src/views/ProgramDetail.test.tsx
git commit -m "feat(wiki): route the wiki view and link it from the program page"
```

---

## Done when

- `~/venvs/coscience/bin/python -m pytest` passes, with the ~60 new backend tests.
- `cd frontend && npm test` passes, with the new `wikiPage` and `WikiView` tests, and `npx tsc --noEmit` is clean.
- `GET /api/programs/<pid>/wiki` returns counts, trust breakdown, pending count and the last run for a real bundle.
- In the dashboard at `/programs/<pid>/wiki` you can read `index.md`, click into a page, follow an internal link without leaving the app, click a provenance chip through to the result or artifact it cites, mark a page verified, change its status, and write its `# Human notes`.
- No path outside the bundle is reachable through any `{slug:path}` route — the traversal and symlink tests prove it.
- Phase 1's behaviour is unchanged: no test in `tests/ -k wiki` from phase 1 was edited to accommodate phase 2.

## Not in this phase

Agent lint mode, the lint cadence going live, the lint report UI and quarantine retry from a run (phase 3). `wiki_graph`, `/wiki/graph`, `WikiGraphView`, `d3-force`, provenance backlinks on `SprintDetail` / `ArtifactDetail` (phase 4). Wiki chat, research runs, the `QUESTIONS.md` flow, MCP `wiki_search` / `wiki_read` / `wiki_neighbors`, "consult the wiki first" in the sprint worker prompt (phase 5). The `wiki_model` / `wiki_enabled` controls in `ProgramSettingsModal` — phase 1's plan deferred them to "phase 2" in its own *Not in this phase* section, though spec §15's phase-2 row does not list them. They are a settings-modal change independent of browse, so do them as a small follow-up rather than letting them block the milestone. Note the modal is `ProgramSettingsModal.tsx`, which is *not* on the do-not-commit list, so that follow-up is unblocked.

## Open questions for the human

1. ~~**`ProgramDetail.tsx`** — Task 15.~~ **Resolved 2026-08-21:** the carried-over frontend work was fixed and committed to `main` as `ee062d2`, so Task 15 is unblocked and D3/D4/D5 were rewritten accordingly. `main` was then merged into this branch as `fe0c125`, so every task below edits `ProgramDetail.tsx`, `SprintDetail.tsx` and `styles.css` at their current revisions and `PageToc.tsx` is present on the branch. Nothing here works against a stale tree.
2. **The two open phase-1 defects** (agent writing into `# Human notes`; `substrate.commit()` being repo-wide) both touch phase 2's surface: the notes editor writes the section an agent has been seen to overwrite, and every curation action commits. Neither blocks this plan, but a decision before Task 5 would be better than after.
3. **`POST /wiki/run` and authentication.** The endpoint spends Claude quota, and `verify` is the only route in this plan that reads the session user. If forcing a run should require an authenticated user, say so and Task 9 gains a `Depends(current_user)` guard.

---

## Execution record — 2026-08-21

All 15 tasks implemented. **Backend 1107 passed** (1044 before this plan, +63),
**frontend 147 passed** (110 before, +37), `tsc --noEmit` clean, `vite build`
clean. Nothing in phase 1's suite was edited to accommodate phase 2.

Verified per layer: `wiki_read` 21, `Service` 28, HTTP 15, `api.ts` + `wikiPage.ts`
30, `WikiView` 19, `ProgramDetail` 7 (5 pre-existing + 2 new).

### Where the plan was wrong, and what it cost

Four defects, all in the plan's *test* code rather than its implementation code.
Each is fixed above, in place, so this file no longer teaches the mistake.

1. **`@testing-library/user-event` is not a dependency of this project.** The plan
   imported it; `package.json` has only `@testing-library/react` and `vitest`, and
   every existing test uses `fireEvent`. Installing it was not an option either —
   `frontend/node_modules` is Avatar's Linux install arriving over Syncthing, so
   `npm install` here would have replaced its platform binaries. This is now a
   Global Constraint. The behavioural difference matters: `userEvent.type` fires
   per keystroke, so a search box would receive `l`, `le`, `lea`… while
   `fireEvent.change` delivers the whole query once.
2. **An excerpt test whose body was shorter than the excerpt window.** ±80 chars
   around a hit in a 140-char body covers the whole body, so "the excerpt is
   shorter than the body" could never hold. The implementation was right.
3. **`getByText(/Definition/)` matched two elements** once the outline pane renders
   beside the body — the markdown `<h1>` and the outline link. Fixed by querying
   the heading role.
4. **`queryByText("0")` matched an unrelated `<span class="mono">`** on
   `ProgramDetail`, which renders many counts. Both badge assertions now scope to
   the wiki link's own `textContent`.

The pattern in 2–4 is one mistake: assertions that were true of the behaviour but
ambiguous about *where* they looked. In a three-pane view inside a page full of
counts, a global text query is a coin flip. Prefer role-scoped or element-scoped
queries.

### Deviations from the plan as written

- **Tasks 12–14 were executed as one unit** with a single combined test file.
  Each WSL test run costs 80–200s, so six red/green cycles would have spent ~10
  minutes of wall clock for no extra safety. Still red-then-green, in two runs.
- **The `ProgramDetail` wiki link became its own card**, not a link in the
  artifacts card's header row as Task 15 described. A wiki link inside a card
  labelled "artifacts" reads wrong; §11.2 says "beside ideas and artifacts", and
  those each own a card with their own `open X →` link. The new card carries the
  pending count as a badge and one line saying what the wiki is.
- **`{ id: "sec-wiki", label: "Wiki" }` was added to `ProgramDetail`'s ToC
  entries.** `PageToc` builds its nav from that hardcoded list, so a new `sec-*`
  card absent from the list is a section the nav silently omits.

### Environment note for whoever picks this up

This was executed with the sandbox unavailable — its disk hit 100%. The suites ran
in **WSL Ubuntu** against `/mnt/d`, with two setup quirks worth knowing: the distro
has no `python3-venv`/`ensurepip` and `sudo` needs a password, so pip was
bootstrapped with `get-pip.py`; and Node was unpacked to `~/opt/node` and invoked
as `node node_modules/vitest/vitest.mjs`, because `node_modules/.bin` symlinks do
not survive Syncthing. `mcp` is pinned `<2` there for the same reason as on the
sandbox — `pyproject.toml:10` allows 2.0.0, which removed `mcp.server.fastmcp`.

**Watch the line endings.** `.gitattributes` is `* text=auto eol=lf` specifically
to stop CRLF churn across synced machines. Python's `write_text` on Windows emits
`\r\n`, which git normalises at commit but Syncthing propagates *before* any
commit — so 8 files were briefly CRLF in the working tree. Use heredocs or an
editor tool, not `pathlib.write_text`, when editing from Windows.

## Still open — closed out 2026-08-26

Nothing. Phase 2 is done. What this section listed, and where each went:

- **The two phase-1 defects.** The agent writing into `# Human notes` is **fixed**
  (`b33be05`): footnote definitions now have their own `# References` home, the
  prompt says so, and two lint rules catch it from either direction. The repo-wide
  `substrate.commit()` is **deferred by decision** — it is platform behaviour, not
  wiki behaviour, and path-scoped commits touch every writer. Recorded in the
  charter's §2 item 5 and its decision log.
- **`POST /wiki/run` and authentication.** The premise was wrong: the route was
  never unauthenticated. It hangs off the `api` router, which carries
  `dependencies=[Depends(current_user)]` and 401s every route on it whenever a user
  registry exists — `test_forcing_a_run_requires_a_logged_in_user` now pins that.
  The real gap was **attribution**: a forced run spends a Claude window and the run
  said nothing about whose say-so it was. It now records `forced_by`, built
  server-side from the session like `verify`'s actor, and the running badge names
  them. An unattended beat leaves the field absent rather than claiming a person.
- **The manual browse.** **Done**, on a live bundle, in a session of its own. It
  earned its keep: it found the four read-path defects fixed in `9b18510` and
  `975cb25` — raw frontmatter rendered as the index's largest heading, unroutable
  `cited from` chips, footnote definitions pre-filling the curation textarea, and a
  reading column squeezed to 448px — none of which the 1280-test suite could see.
  The plan document (`docs/knowledge/phase-2-ui-test-plan.md`) has been deleted now
  that it has been executed; what it found lives in those commits and in the
  charter's §2.

The milestone is complete and **unmerged by choice**. Merge and deploy are the
human's call and have not been given.
