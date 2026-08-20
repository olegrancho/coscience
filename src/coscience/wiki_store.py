"""Bundle IO for a program wiki.

`programs/<pid>/wiki/` is the OKF bundle: portable, the actual product, and an
Obsidian vault as-is. `programs/<pid>/.wiki/` is its sibling holding derived
machine state — so copying the bundle anywhere yields something valid with
nothing to strip. Everything here touches the substrate filesystem; the thinking
lives in the pure modules (wiki_okf, wiki_lint, wiki_prompts)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from coscience import wiki_okf
from coscience.frontmatter_io import serialize as _serialize_frontmatter

PAGE_DIRS = ("concepts", "entities", "syntheses", "sources")

BUNDLE_CLAUDE_MD = """# This wiki

You are working inside a program's knowledge wiki: a set of interlinked markdown
pages compiled from that program's sprint results and artifacts. Read this file
before writing anything.

## Layout

- `index.md` — the map of the wiki. Reserved; keep its frontmatter.
- `log.md` — append-only chronology, newest first. Reserved.
- `QUESTIONS.md` — open and resolved questions. Reserved.
- `concepts/` — reusable abstractions: mechanisms, methods, phenomena, regimes,
  parameters, frameworks.
- `entities/` — specific named things: people, tools, models, datasets,
  organisms, genes, instruments, papers.
- `syntheses/` — cross-source insight bundles that are valuable but are not
  durable standalone nodes.
- `sources/` — grounding pages: one per ingested result or artifact version.
  These point at the raw object; they are never concepts.

## Every page

```yaml
---
type: Concept                    # Concept | Entity | Synthesis | Source | Question
title: Template replication takeoff
description: One sentence stating what this page is.
tags: [abiogenesis, kinetics]
status: draft                    # lifecycle: draft | stable | deprecated
stale_after: 2027-02-20          # optional declared expiry
generated: { by: coscience-wiki/<model>, at: <ISO-8601 UTC> }
sources:
  - { id: c14, resource: /sources/result-<id>.md, title: "...", last_modified: 2026-08-14 }
relations:
  - { type: requires, target: /concepts/hydrolysis-rate.md, confidence: high, source: c14 }
aliases: [takeoff threshold]
---

# Definition
# Evidence
# Contradictions
# Open questions
# Human notes
```

`type` is the only required field. Never invent a `verified:` entry — trust is
recorded by the platform when a human marks a page verified, never by you.

## Relations — the vocabulary is frozen

`is_a`, `part_of`, `requires`, `enables`, `implements`, `exemplifies`,
`measures`, `causally_precedes`, `contradicts`, `refines`, `replaces`,
`extends`. Anything else is a lint error.

Every relation carries `confidence` (`low` | `med` | `high`) and `source`, which
names an id from this page's `sources` list. **A relation with no source is a
lint error** — an unattributed assertion is exactly what this wiki exists to
prevent.

**The containment invariant:** every typed relation's target must ALSO be linked
from the page body, as a normal markdown link. The frontmatter is the typed
overlay; the prose is the substrate. A relation the prose does not mention is a
lint error.

## Rules that bite

1. **Merge first.** Before creating a page, search for an existing one covering
   the same idea — check titles AND `aliases`. Extend it and add an alias rather
   than creating a near-duplicate.
2. **Over-extract.** A concept mentioned once is still worth a page; a concept
   never written down is knowledge lost.
3. **A source title is never a concept.** "Sprint p3-c14 result" is a source
   page. The concepts are what it established.
4. **Attribute per claim.** Use markdown footnotes (`[^c14]`) tied to
   `sources[].id`.
5. **`# Human notes` is protected.** If a page has one, reproduce it byte for
   byte. It is a human's correction and outranks anything you would write.
6. **Never delete a page.** Propose merges in the run report; a human decides.
7. **Never compute a content hash.** The platform hands you `origin_hash` values;
   copy them exactly. A hash you invent cannot detect drift.
8. **Never write outside this directory.**
"""

_INDEX_BODY = """# {title}

This wiki is compiled from the program's results and artifacts. Pages are grouped
below as they are written.

## Concepts

## Entities

## Syntheses
"""


def _index_md(title: str) -> str:
    """Build index.md through the repo's YAML serializer, not a hand-templated
    string — a title containing a colon or a leading YAML-significant character
    (#, -, ?, *, &, !, %, @, backtick) would otherwise produce unparseable
    frontmatter."""
    fm = {
        "type": "Index",
        "title": title,
        "okf_version": "0.2",
        "description": "Knowledge compiled from this program's sprint results and artifacts.",
    }
    return _serialize_frontmatter(fm, _INDEX_BODY.format(title=title))


_LOG_MD = """# Log

Newest first. One line per ingest or lint run.
"""

_QUESTIONS_MD = """# Questions

## Open

## Resolved
"""


def bundle_dir(substrate, program_id: str) -> Path:
    return substrate.program_dir(program_id) / "wiki"


def state_dir(substrate, program_id: str) -> Path:
    return substrate.program_dir(program_id) / ".wiki"


def run_dir(substrate, program_id: str, run_id: str) -> Path:
    return state_dir(substrate, program_id) / "runs" / run_id


def ensure_bundle(substrate, program_id: str) -> Path:
    """Create the bundle skeleton if it is missing. Idempotent, and never
    overwrites a file that exists — the log and the index accumulate content."""
    bundle = bundle_dir(substrate, program_id)
    bundle.mkdir(parents=True, exist_ok=True)
    for d in PAGE_DIRS:
        (bundle / d).mkdir(exist_ok=True)
    state_dir(substrate, program_id).mkdir(parents=True, exist_ok=True)
    try:
        title = substrate.load_program(program_id).title or program_id
    except (OSError, ValueError):
        title = program_id
    for name, text in (("index.md", _index_md(f"{title} — wiki")),
                       ("log.md", _LOG_MD),
                       ("QUESTIONS.md", _QUESTIONS_MD),
                       ("CLAUDE.md", BUNDLE_CLAUDE_MD)):
        f = bundle / name
        if not f.exists():
            f.write_text(text)
    return bundle


def page_paths(substrate, program_id: str) -> list[str]:
    """Bundle-relative paths of every page, sorted. Reserved files are excluded."""
    bundle = bundle_dir(substrate, program_id)
    out: list[str] = []
    for d in PAGE_DIRS:
        sub = bundle / d
        if not sub.is_dir():
            continue
        for f in sorted(sub.glob("*.md")):
            if f.name not in wiki_okf.RESERVED:
                out.append(f"{d}/{f.name}")
    return out


def read_page(substrate, program_id: str, rel: str) -> wiki_okf.Page | None:
    f = bundle_dir(substrate, program_id) / rel
    try:
        text = f.read_text()
    except OSError:
        return None
    return wiki_okf.parse_page(rel, text)


def iter_pages(substrate, program_id: str) -> list[wiki_okf.Page]:
    pages = [read_page(substrate, program_id, rel)
             for rel in page_paths(substrate, program_id)]
    return [p for p in pages if p is not None]


def write_page(substrate, program_id: str, page: wiki_okf.Page) -> Path:
    f = bundle_dir(substrate, program_id) / page.path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(wiki_okf.render_page(page))
    return f


def is_empty(substrate, program_id: str) -> bool:
    return not page_paths(substrate, program_id)


@dataclass
class WikiObject:
    """One ingestable raw object: a sprint result or an artifact version."""
    oid: str
    kind: str
    title: str
    at: float = 0.0
    paths: list[Path] = field(default_factory=list)
    resource: str = ""
    slug: str = ""


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return f"sha256:{h.hexdigest()}"


def hash_dir(path: Path) -> str:
    """Digest of a directory: sha256 over the sorted (relpath, file-sha256) list.
    Deterministic across machines; insensitive to mtime and to walk order."""
    if not path.is_dir():
        return ""
    entries = []
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = f.relative_to(path).as_posix()
        entries.append(f"{rel}\0{hash_file(f)}")
    h = hashlib.sha256("\n".join(entries).encode())
    return f"sha256:{h.hexdigest()}"


def object_hash(obj: WikiObject) -> str:
    """"" when the object's bytes are gone — the caller reads that as src/missing."""
    if obj.kind == "result":
        return hash_file(obj.paths[0]) if obj.paths else ""
    return hash_dir(obj.paths[0]) if obj.paths else ""


def program_objects(substrate, program_id: str) -> list[WikiObject]:
    """Every ingestable object belonging to this program, oldest first.

    Results resolve to a program through their sprint; a result whose sprint is
    missing or belongs elsewhere is skipped rather than guessed at. Artifacts
    contribute their CURRENT version only, so a figure revised five times leaves
    one page trail instead of five near-identical source pages."""
    out: list[WikiObject] = []
    for result in substrate.iter_results():
        try:
            sprint = substrate.load_sprint(result.sprint)
        except Exception:
            continue
        if sprint.program != program_id:
            continue
        out.append(WikiObject(
            oid=f"result:{result.id}", kind="result",
            title=(sprint.title or sprint.goals or result.id).strip()[:120],
            at=float(result.completed_at or 0.0),
            paths=[substrate.repo_root / "results" / f"{result.id}.md"],
            resource=f"/results/{result.id}.md",
            slug=f"sources/result-{result.id}.md"))
    for art in substrate.iter_artifacts(program_id):
        vid = art.current
        if not vid:
            continue
        version = next((v for v in art.versions if v.id == vid), None)
        if version is None or version.archived:
            continue
        out.append(WikiObject(
            oid=f"artifact:{art.id}@{vid}", kind="artifact",
            title=(art.title or art.id).strip()[:120],
            at=float(version.created_at or 0.0),
            paths=[substrate.artifact_dir(program_id, art.id) / vid],
            resource=f"/programs/{program_id}/artifacts/{art.id}/{vid}",
            slug=f"sources/artifact-{art.id}-{vid}.md"))
    out.sort(key=lambda o: (o.at, o.oid))
    return out


def pending_objects(substrate, program_id: str, ingested: dict[str, dict],
                    quarantined: set[str] | None = None) -> list[WikiObject]:
    """Objects needing ingest, oldest first: never ingested, or ingested under a
    hash that no longer matches. One code path covers both, which is why drift
    can never go unnoticed."""
    skip = quarantined or set()
    out = []
    for obj in program_objects(substrate, program_id):
        if obj.oid in skip:
            continue
        known = (ingested.get(obj.oid) or {}).get("hash", "")
        current = object_hash(obj)
        if not current:
            continue                       # the bytes are gone; lint reports src/missing
        if known != current:
            out.append(obj)
    return out
