"""Plan a merge of two wiki pages into one.

Pure — no IO. `plan` returns the rewritten pages; writing them, deleting the
loser and committing is the caller's job (Service.merge_wiki_pages).

Why the agent never does this itself: merging means deleting a page, which every
wiki prompt forbids and which would need its own trust argument. The agent
proposes; the platform performs. Spec 9.1.

Pure means the caller's objects are never touched: `plan` builds new `Page`
objects for both `winner` and every entry in `rewritten`, so a caller that
inspects `winner`/`loser`/`others` after the call sees exactly what it passed
in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from coscience import wiki_okf


@dataclass
class MergePlan:
    winner: wiki_okf.Page
    rewritten: list[wiki_okf.Page] = field(default_factory=list)
    loser_path: str = ""


def _rel_key(r: wiki_okf.Relation) -> tuple[str, str]:
    return (r.type, (r.target or "").split("#", 1)[0].strip().lstrip("/"))


def _dedupe(relations: list[wiki_okf.Relation]) -> list[wiki_okf.Relation]:
    seen, out = set(), []
    for r in relations:
        key = _rel_key(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _headings(body: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"(?m)^# +(.+?)\s*$", body)]


def _set_section(body: str, heading: str, text: str) -> str:
    """Replace the text under `# <heading>`, appending the section if absent.

    Same view of a section as wiki_okf.Page.section — heading to the next `# ` —
    so a read after a write returns what was written."""
    marks = [(m.group(1), m.start()) for m in re.finditer(r"(?m)^# +(.+?)\s*$", body)]
    block = f"# {heading}\n\n{text.strip()}\n"
    for i, (name, start) in enumerate(marks):
        if name.strip().lower() == heading.strip().lower():
            stop = marks[i + 1][1] if i + 1 < len(marks) else len(body)
            return body[:start] + block + ("\n" + body[stop:] if stop < len(body) else "")
    return (body.rstrip("\n") + "\n\n" + block) if body.strip() else block


def _merge_bodies(winner: wiki_okf.Page, loser: wiki_okf.Page) -> str:
    """Stack the loser's sections under the winner's matching headings.

    Deliberately mechanical and deliberately rough: a script cannot write one
    definition from two. The `merged_from` marker set below raises
    page/unmerged-prose until a lint run rewrites it into one voice."""
    body = winner.body
    for heading in _headings(loser.body):
        if heading.strip().lower() == "human notes":
            continue                       # handled separately, and labelled
        text = loser.section(heading)
        if not text:
            continue
        existing = winner.section(heading) if winner.has_section(heading) else ""
        body = _set_section(body, heading, f"{existing}\n\n{text}".strip())
    return body


def _merge_notes(body: str, winner: wiki_okf.Page, loser: wiki_okf.Page) -> str:
    """Both pages' notes survive, each labelled. Spec 9.1."""
    theirs = loser.section("Human notes")
    if not theirs:
        return body
    mine = winner.section("Human notes")
    label = f"*from {loser.slug}:*"
    return _set_section(body, "Human notes", f"{mine}\n\n{label}\n\n{theirs}".strip())


def _relink(body: str, loser: wiki_okf.Page, winner: wiki_okf.Page) -> str:
    for old in (f"/{loser.path}", loser.path):
        body = body.replace(f"]({old})", f"](/{winner.path})")
    return re.sub(r"\[\[\s*" + re.escape(loser.slug) + r"\s*(\||\]\])",
                  lambda m: f"[[{winner.slug}" + m.group(1), body)


def plan(winner: wiki_okf.Page, loser: wiki_okf.Page,
         others: list[wiki_okf.Page]) -> MergePlan:
    """Merge `loser` into `winner`. `others` is every other page in the bundle.

    Raises ValueError for a merge the wiki must never perform. Never mutates
    `winner`, `loser` or any page in `others` — the returned `winner` and every
    entry in `rewritten` are new objects."""
    if winner.path == loser.path:
        raise ValueError("cannot merge a page into itself")
    if "Source" in (winner.type, loser.type):
        raise ValueError("source pages are bound to a real object and never merge")

    merged = wiki_okf.Page(**{**winner.__dict__})
    merged.relations = list(winner.relations)
    merged.sources = list(winner.sources)
    merged.aliases = list(winner.aliases)
    merged.extra = dict(winner.extra)
    merged.tags = list(winner.tags)
    merged.generated = dict(winner.generated)

    merged.body = _merge_notes(_merge_bodies(winner, loser), winner, loser)
    merged.verified = []

    rels = [r for r in (list(winner.relations) + list(loser.relations))
            if _rel_key(r)[1] not in (loser.path, winner.path)]
    merged.relations = _dedupe(rels)

    by_id = {s.id: s for s in merged.sources}
    for s in loser.sources:
        if s.id not in by_id:
            by_id[s.id] = s
            merged.sources.append(s)

    for alias in [loser.title, loser.slug, *loser.aliases]:
        if alias and alias not in merged.aliases:
            merged.aliases.append(alias)

    marker = [str(x) for x in (merged.extra.get("merged_from") or [])]
    merged.extra["merged_from"] = marker + [loser.slug]

    rewritten = []
    for page in others:
        if page.path in (winner.path, loser.path):
            continue
        body = _relink(page.body, loser, winner)
        touched_rel = any(_rel_key(r)[1] == loser.path for r in page.relations)
        if touched_rel:
            rels = []
            for r in page.relations:
                if _rel_key(r)[1] == loser.path:
                    rels.append(wiki_okf.Relation(
                        type=r.type, target=f"/{winner.path}",
                        confidence=r.confidence, source=r.source,
                        extra=dict(r.extra)))
                else:
                    rels.append(r)
            rels = _dedupe(rels)
        else:
            rels = list(page.relations)
        if body == page.body and not touched_rel:
            continue
        new_page = wiki_okf.Page(**{**page.__dict__})
        new_page.body = body
        new_page.relations = rels
        new_page.sources = list(page.sources)
        new_page.aliases = list(page.aliases)
        new_page.extra = dict(page.extra)
        new_page.tags = list(page.tags)
        new_page.generated = dict(page.generated)
        new_page.verified = list(page.verified)
        rewritten.append(new_page)

    return MergePlan(winner=merged, rewritten=rewritten, loser_path=loser.path)
