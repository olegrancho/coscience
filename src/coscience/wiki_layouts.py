"""Which layout a program wiki is in, which one is current, and how to get there.

A layout is a (name, version) pair: the page schema the bundle's CLAUDE.md states
and the rules its ingests follow. The bundle records its own layout on the first
line of CLAUDE.md —

    <!-- coscience-wiki-layout: topics v1 -->

— so the record lives in the substrate and every instance that serves it reads the
same answer. A bundle with no marker predates the scheme and is `concepts` v1.

Changing the wiki's shape later means: a new (name, version) with its schema text,
`CURRENT` pointing at it, and an entry in `UPGRADES` from the previous one. See
docs/wiki-layouts.md."""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple


class Layout(NamedTuple):
    name: str
    version: int

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


CONCEPTS_V1 = Layout("concepts", 1)   # one page per concept; ingests append
TOPICS_V1 = Layout("topics", 1)       # topic pages opening with the current understanding

CURRENT = TOPICS_V1

#: layout -> the layout its migration produces. A wiki several versions behind
#: steps through them one migration at a time.
UPGRADES: dict[Layout, Layout] = {
    CONCEPTS_V1: TOPICS_V1,
}

_MARKER = re.compile(r"<!--\s*coscience-wiki-layout:\s*([a-z0-9_-]+)\s+v(\d+)\s*-->")


def marker(layout: Layout) -> str:
    return f"<!-- coscience-wiki-layout: {layout.name} v{layout.version} -->"


def read(bundle: Path) -> Layout | None:
    """The layout a bundle is in, or None when it has no CLAUDE.md yet."""
    try:
        text = (Path(bundle) / "CLAUDE.md").read_text()
    except OSError:
        return None
    m = _MARKER.search(text.split("\n", 3)[0] if text else "")
    if m:
        return Layout(m.group(1), int(m.group(2)))
    # Lab copies were written before the marker existed; their schema says so.
    if "organised by topic" in text:
        return TOPICS_V1
    return CONCEPTS_V1


def of(bundle: Path) -> Layout:
    """The layout runs on this bundle follow: its own, or CURRENT for a new one."""
    return read(bundle) or CURRENT


def schema(layout: Layout) -> str:
    from coscience import wiki_store, wiki_topics
    if layout == TOPICS_V1:
        return wiki_topics.SCHEMA
    return wiki_store.BUNDLE_CLAUDE_MD


def next_after(layout: Layout) -> Layout | None:
    """The layout one migration away, or None when `layout` is current or unknown."""
    return UPGRADES.get(layout)
