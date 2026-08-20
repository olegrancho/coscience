"""The Open Knowledge Format page model for a program wiki bundle.

Pure — no IO. OKF v0.2 requires exactly one thing of a page (parseable
frontmatter with a non-empty `type`) and requires consumers to tolerate unknown
types, unknown keys and broken links. So this parser never raises: a page whose
YAML is broken comes back with `bad_yaml=True` and an empty type, and every key
we do not model is carried in `extra` and rendered back out untouched."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from coscience.frontmatter_io import parse as _parse_frontmatter
from coscience.frontmatter_io import serialize

RELATION_TYPES = frozenset({
    "is_a", "part_of", "requires", "enables", "implements", "exemplifies",
    "measures", "causally_precedes", "contradicts", "refines", "replaces",
    "extends",
})
PAGE_TYPES = frozenset({"Concept", "Entity", "Synthesis", "Source", "Question"})
CONFIDENCE = frozenset({"low", "med", "high"})

# Reserved bundle files: no page frontmatter is required of them.
RESERVED = ("index.md", "log.md", "CLAUDE.md", "QUESTIONS.md")

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)[^)]*\)")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_SECTION = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

_RELATION_KEYS = ("type", "target", "confidence", "source")
_SOURCE_KEYS = ("id", "resource", "title", "last_modified")
_PAGE_KEYS = ("type", "title", "description", "tags", "status", "stale_after",
              "generated", "verified", "sources", "relations", "aliases",
              "graph_excluded")


@dataclass
class Relation:
    type: str = ""
    target: str = ""
    confidence: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Source:
    id: str = ""
    resource: str = ""
    title: str = ""
    last_modified: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Page:
    path: str                              # bundle-relative, POSIX separators
    type: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = ""
    stale_after: str = ""
    generated: dict = field(default_factory=dict)
    verified: list[dict] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    graph_excluded: bool = False
    body: str = ""
    extra: dict = field(default_factory=dict)
    bad_yaml: bool = False

    @property
    def slug(self) -> str:
        return self.path.rsplit("/", 1)[-1][:-3] if self.path.endswith(".md") else self.path

    def section(self, heading: str) -> str:
        """The body text under `# <heading>`, up to the next `# ` heading."""
        marks = [(m.group(1), m.start(), m.end()) for m in _SECTION.finditer(self.body)]
        for i, (name, _start, end) in enumerate(marks):
            if name.strip().lower() == heading.strip().lower():
                stop = marks[i + 1][1] if i + 1 < len(marks) else len(self.body)
                return self.body[end:stop].strip()
        return ""

    def has_section(self, heading: str) -> bool:
        return any(m.group(1).strip().lower() == heading.strip().lower()
                   for m in _SECTION.finditer(self.body))

    def sources_by_id(self) -> dict[str, Source]:
        return {s.id: s for s in self.sources if s.id}


def _str(v) -> str:
    return "" if v is None else str(v)


def _strlist(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    return [_str(x) for x in v] if isinstance(v, list) else []


def _split(raw: dict, known: tuple[str, ...]) -> dict:
    return {k: v for k, v in raw.items() if k not in known}


def parse_page(path: str, text: str) -> Page:
    """Parse one bundle page. Never raises."""
    try:
        fm, body = _parse_frontmatter(text)
    except yaml.YAMLError:
        return Page(path=path, body=text, bad_yaml=True)
    if not isinstance(fm, dict):
        return Page(path=path, body=body, bad_yaml=True)
    relations = []
    for r in fm.get("relations") or []:
        if not isinstance(r, dict):
            continue
        relations.append(Relation(
            type=_str(r.get("type")), target=_str(r.get("target")),
            confidence=_str(r.get("confidence")), source=_str(r.get("source")),
            extra=_split(r, _RELATION_KEYS)))
    sources = []
    for s in fm.get("sources") or []:
        if not isinstance(s, dict):
            continue
        sources.append(Source(
            id=_str(s.get("id")), resource=_str(s.get("resource")),
            title=_str(s.get("title")), last_modified=_str(s.get("last_modified")),
            extra=_split(s, _SOURCE_KEYS)))
    gen = fm.get("generated")
    ver = fm.get("verified")
    return Page(
        path=path,
        type=_str(fm.get("type")),
        title=_str(fm.get("title")),
        description=_str(fm.get("description")),
        tags=_strlist(fm.get("tags")),
        status=_str(fm.get("status")),
        stale_after=_str(fm.get("stale_after")),
        generated=gen if isinstance(gen, dict) else {},
        verified=[v for v in (ver or []) if isinstance(v, dict)],
        sources=sources,
        relations=relations,
        aliases=_strlist(fm.get("aliases")),
        graph_excluded=bool(fm.get("graph_excluded", False)),
        body=body,
        extra=_split(fm, _PAGE_KEYS),
    )


def render_page(page: Page) -> str:
    """Render back to markdown. Key order is the §6 template order, with unknown
    keys appended so nothing an agent wrote is lost."""
    fm: dict = {"type": page.type}
    for key, value in (("title", page.title), ("description", page.description),
                       ("status", page.status), ("stale_after", page.stale_after)):
        if value:
            fm[key] = value
    if page.tags:
        fm["tags"] = list(page.tags)
    if page.generated:
        fm["generated"] = dict(page.generated)
    if page.verified:
        fm["verified"] = [dict(v) for v in page.verified]
    if page.sources:
        fm["sources"] = [_clean({"id": s.id, "resource": s.resource, "title": s.title,
                                 "last_modified": s.last_modified}, s.extra)
                         for s in page.sources]
    if page.relations:
        fm["relations"] = [_clean({"type": r.type, "target": r.target,
                                   "confidence": r.confidence, "source": r.source},
                                  r.extra)
                           for r in page.relations]
    if page.aliases:
        fm["aliases"] = list(page.aliases)
    if page.graph_excluded:
        fm["graph_excluded"] = True
    fm.update(page.extra)
    return serialize(fm, page.body)


def _clean(fields: dict, extra: dict) -> dict:
    out = {k: v for k, v in fields.items() if v not in ("", None)}
    out.update(extra)
    return out


def body_links(body: str) -> list[str]:
    """Markdown link targets in document order. Image links are excluded."""
    return [m.group(1) for m in _LINK.finditer(body)]


def wikilinks(body: str) -> list[str]:
    """`[[slug]]` targets — the shape lint rewrites into real markdown links."""
    return [m.group(1).strip() for m in _WIKILINK.finditer(body)]
