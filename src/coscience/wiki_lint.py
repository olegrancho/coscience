"""Wiki lint: pure rules over parsed pages.

Two consumers, one implementation. As a script it is cheap, deterministic and
gives the dashboard a health badge; as the input to an agent lint run it is the
list of things a machine could find so the agent can spend its turn on the things
only judgement can fix.

Ported from docs/_tmp_wiki/llm-wiki-skills/skills/llm-wiki-lint/lint.py and
extended for OKF and for the containment invariant."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from coscience import wiki_okf

SEVERITIES = ("error", "warn", "info")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

STUB_CHARS = 200
STALE_DAYS = 180
_NORMALISE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    message: str


def lint(pages: list[wiki_okf.Page], *, index_body: str = "",
         objects: dict[str, str] | None = None,
         previous: dict[str, str] | None = None,
         now: float | None = None) -> list[Finding]:
    """Every finding for this bundle, worst first. Never raises — a lint that
    dies on a malformed page is a lint that stops running exactly when it is
    needed."""
    out: list[Finding] = []
    out += _okf_rules(pages)
    out += _page_rules(pages, index_body, now)
    out += _link_rules(pages)
    out += _relation_rules(pages)
    out += _source_rules(pages, objects)
    out += _trust_rules(pages, previous)
    out += _human_notes_rules(pages)
    out.sort(key=lambda f: (_RANK.get(f.severity, 9), f.path, f.rule))
    return out


def _okf_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    out = []
    for p in pages:
        if p.bad_yaml:
            out.append(Finding("okf/bad-yaml", "error", p.path,
                               "frontmatter does not parse as YAML"))
            continue
        if not p.type.strip():
            out.append(Finding("okf/missing-type", "error", p.path,
                               "no `type` — the page is not OKF-conformant"))
        if p.path.rsplit("/", 1)[-1] == "index.md" and p.path != "index.md":
            out.append(Finding("okf/index-frontmatter", "warn", p.path,
                               "a non-root index.md should not carry page frontmatter"))
    return out


def _page_rules(pages: list[wiki_okf.Page], index_body: str,
                now: float | None) -> list[Finding]:
    out = []
    index_linked = _linked_targets(index_body)
    # Per-page, so a page's own outbound links can be excluded when testing
    # that same page — otherwise a page whose only "inbound" link is a
    # self-link would wrongly count as not-orphaned.
    page_linked = {p.path: _linked_targets(p.body) for p in pages}
    by_slug: dict[str, list[str]] = {}
    titles: dict[str, str] = {}

    for p in pages:
        if len(p.body.strip()) < STUB_CHARS:
            out.append(Finding("page/stub", "warn", p.path,
                               f"body is under {STUB_CHARS} characters"))
        stale = _staleness(p, now)
        if stale:
            out.append(Finding("page/stale", "warn", p.path, stale))
        inbound = set(index_linked)
        for other_path, targets in page_linked.items():
            if other_path != p.path:
                inbound.update(targets)
        if not _is_linked(p, inbound):
            out.append(Finding("page/orphan", "info", p.path,
                               "no inbound links and absent from index.md"))
        by_slug.setdefault(p.slug, []).append(p.path)
        for name in [p.title, *p.aliases]:
            key = _norm(name)
            if not key:
                continue
            other = titles.get(key)
            if other and other != p.path:
                out.append(Finding("page/near-duplicate", "warn", p.path,
                                   f"title or alias '{name}' also names {other}"))
            titles.setdefault(key, p.path)

    for slug, paths in by_slug.items():
        if len(paths) > 1:
            for path in paths:
                out.append(Finding("page/duplicate-slug", "error", path,
                                   f"slug '{slug}' also exists at "
                                   + ", ".join(p for p in paths if p != path)))
    return out


def _linked_targets(body: str) -> set[str]:
    """Every link target Approach C treats as first-class link substrate:
    plain markdown links AND `[[wikilinks]]`. A rule that only sees one half
    of the link surface reports pages orphaned that are perfectly linked."""
    return set(wiki_okf.body_links(body)) | set(wiki_okf.wikilinks(body))


def _is_linked(page: wiki_okf.Page, linked: set[str]) -> bool:
    """Markdown links are written bundle-absolute (/concepts/a.md) but may
    appear relative — accept either. Wikilinks are written bare ([[slug]]),
    so also accept an exact slug match. Whichever form actually appears,
    don't cry orphan on a working link."""
    for target in linked:
        if target.lstrip("/").endswith(page.path) or target.endswith(page.path):
            return True
        if target == page.slug:
            return True
    return False


def _norm(text: str) -> str:
    return _NORMALISE.sub("-", (text or "").strip().lower()).strip("-")


def _staleness(page: wiki_okf.Page, now: float | None) -> str:
    if now is None:
        return ""
    today = datetime.fromtimestamp(now, timezone.utc)
    declared = _date(page.stale_after)
    if declared is not None:
        return (f"stale_after {page.stale_after} has passed"
                if declared < today else "")
    generated = _date(str(page.generated.get("at", "")))
    if generated is not None and (today - generated).days > STALE_DAYS:
        return (f"no stale_after and generated {(today - generated).days} days ago "
                f"(over {STALE_DAYS})")
    return ""


def _date(text: str):
    """Parse a date or ISO timestamp; None when it is absent or unparseable —
    an unreadable date is not a lint finding of its own, it just disables the
    check for that page."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def counts(findings: list[Finding]) -> dict[str, int]:
    out = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.severity in out:
            out[f.severity] += 1
    return out


def render_report(findings: list[Finding]) -> str:
    """A markdown report, grouped by rule so an agent reading it sees one class of
    problem at a time rather than a shuffled list of paths."""
    if not findings:
        return "# Wiki lint\n\nNo findings.\n"
    c = counts(findings)
    lines = ["# Wiki lint", "",
             f"{c['error']} error(s), {c['warn']} warning(s), {c['info']} info.", ""]
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        grouped.setdefault((f.severity, f.rule), []).append(f)
    for (severity, rule) in sorted(grouped, key=lambda k: (_RANK.get(k[0], 9), k[1])):
        items = grouped[(severity, rule)]
        lines.append(f"## `{rule}` — {severity} ({len(items)})")
        lines += [f"- `{f.path}`: {f.message}" for f in items]
        lines.append("")
    return "\n".join(lines)


# Spec §9 also marks `okf/index-frontmatter` auto-fixable. Its fix is to strip a
# page's frontmatter entirely, which needs a raw-write path write_page does not
# have — and stripping frontmatter mechanically is how you lose content. It is
# reported and left to the agent's lint run instead.
AUTOFIXABLE = frozenset({"link/wikilink", "rel/no-link"})

# Directional relations that must stay acyclic. `contradicts` is symmetric in
# meaning and stored on one side only, so a mutual pair is correct, not a cycle.
_ACYCLIC = ("replaces", "causally_precedes")


def _known_paths(pages: list[wiki_okf.Page]) -> set[str]:
    return {p.path for p in pages}


def _resolve(target: str) -> str:
    """A link target as a bundle-relative page path, or "" if it is not one."""
    target = (target or "").split("#", 1)[0].strip()
    if not target or "://" in target or target.startswith("mailto:"):
        return ""
    return target.lstrip("/")


def _link_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    known = _known_paths(pages)
    out = []
    for p in pages:
        for slug in wiki_okf.wikilinks(p.body):
            out.append(Finding("link/wikilink", "warn", p.path,
                               f"`[[{slug}]]` should be a markdown link"))
        for target in wiki_okf.body_links(p.body):
            rel = _resolve(target)
            # OKF requires consumers to tolerate broken links, and in a living
            # wiki a broken link often marks knowledge not yet written. Report,
            # never fail.
            if rel and rel not in known:
                out.append(Finding("link/broken", "warn", p.path,
                                   f"link target `{target}` does not exist"))
    return out


def _relation_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    known = _known_paths(pages)
    out = []
    edges: dict[str, set[str]] = {}
    for p in pages:
        body_targets = {_resolve(t) for t in wiki_okf.body_links(p.body)}
        source_ids = set(p.sources_by_id())
        for r in p.relations:
            if r.type not in wiki_okf.RELATION_TYPES:
                out.append(Finding("rel/unknown-type", "error", p.path,
                                   f"relation type `{r.type}` is outside the frozen "
                                   f"vocabulary"))
            rel = _resolve(r.target)
            if rel and rel not in body_targets:
                out.append(Finding("rel/no-link", "error", p.path,
                                   f"relation `{r.type}` -> `{r.target}` is not linked "
                                   f"from the body"))
            if not r.source:
                out.append(Finding("rel/no-source", "error", p.path,
                                   f"relation `{r.type}` -> `{r.target}` names no source"))
            elif r.source not in source_ids:
                out.append(Finding("rel/no-source", "error", p.path,
                                   f"relation `{r.type}` -> `{r.target}` names unknown "
                                   f"source id `{r.source}`"))
            if r.confidence and r.confidence not in wiki_okf.CONFIDENCE:
                out.append(Finding("rel/unknown-type", "error", p.path,
                                   f"confidence `{r.confidence}` is not low|med|high"))
            if rel and rel not in known:
                out.append(Finding("rel/dangling", "warn", p.path,
                                   f"relation target `{r.target}` does not exist"))
            if r.type in _ACYCLIC and rel:
                edges.setdefault(p.path, set()).add(rel)
    for path in sorted(_cycle_nodes(edges)):
        out.append(Finding("rel/cycle", "info", path,
                           "part of a cycle in `replaces` / `causally_precedes`"))
    return out


def _cycle_nodes(edges: dict[str, set[str]]) -> set[str]:
    """Nodes on at least one directed cycle. Iterative DFS with a colour map —
    the graph is small, but a recursive walk on an adversarial bundle is not
    worth the risk."""
    on_cycle: set[str] = set()
    colour: dict[str, int] = {}
    for start in list(edges):
        if colour.get(start):
            continue
        stack = [(start, iter(sorted(edges.get(start, ()))))]
        path = [start]
        colour[start] = 1
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                colour[node] = 2
                stack.pop()
                path.pop()
                continue
            state = colour.get(nxt, 0)
            if state == 1:
                on_cycle.update(path[path.index(nxt):])
            elif state == 0:
                colour[nxt] = 1
                path.append(nxt)
                stack.append((nxt, iter(sorted(edges.get(nxt, ())))))
    return on_cycle


def _wikilink_index(pages: list[wiki_okf.Page]) -> dict[str, wiki_okf.Page]:
    """Every string an agent might plausibly put inside `[[...]]`, mapped to the
    page it means: the bare slug, the bundle path, and the path without `.md`.

    The first live ingest run wrote `[[concepts/session-based-attribution]]` for
    all 19 of its wikilinks. A slug-only index missed every one of them, so the
    rewrite silently did nothing and the warnings survived a `--fix` pass. Callers
    strip a leading `/` and a trailing `.md` before looking a target up here."""
    index: dict[str, wiki_okf.Page] = {}
    for p in pages:
        index.setdefault(p.path.removesuffix(".md"), p)
    for p in pages:
        index.setdefault(p.slug, p)
    return index


def autofix(pages: list[wiki_okf.Page]) -> tuple[list[wiki_okf.Page], list[Finding]]:
    """Apply the deterministic fixes, returning only the pages that changed.

    These run before an agent lint run so the agent's turn is spent on judgement
    calls rather than on mechanical edits it would do worse and slower."""
    by_ref = _wikilink_index(pages)
    changed: list[wiki_okf.Page] = []
    fixed: list[Finding] = []
    for p in pages:
        body = p.body
        for slug in dict.fromkeys(wiki_okf.wikilinks(body)):
            target = by_ref.get(slug.strip().lstrip("/").removesuffix(".md"))
            if not target:
                continue                     # nothing to point at; leave it for the agent
            # Labelled with the target's slug, not its title: for a bare-slug
            # wikilink that is exactly what the agent wrote, and for a path-form
            # one it beats echoing the whole path back at the reader.
            body = body.replace(f"[[{slug}]]", f"[{target.slug}](/{target.path})")
            fixed.append(Finding("link/wikilink", "warn", p.path,
                                 f"rewrote `[[{slug}]]` as a markdown link"))
        body_targets = {_resolve(t) for t in wiki_okf.body_links(body)}
        missing = []
        for r in p.relations:
            rel = _resolve(r.target)
            if rel and rel not in body_targets and rel not in missing:
                missing.append(rel)
        if missing:
            lines = [f"- `{r.type}` [{_title(pages, rel)}](/{rel})"
                     for r, rel in ((r, _resolve(r.target)) for r in p.relations)
                     if rel in missing]
            body = body.rstrip("\n") + "\n\n# Related\n\n" + "\n".join(dict.fromkeys(lines)) + "\n"
            for rel in missing:
                fixed.append(Finding("rel/no-link", "error", p.path,
                                     f"linked `{rel}` from the body to satisfy containment"))
        if body != p.body:
            p.body = body
            changed.append(p)
    return changed, fixed


def _title(pages: list[wiki_okf.Page], path: str) -> str:
    for p in pages:
        if p.path == path:
            return p.title or p.slug
    return path.rsplit("/", 1)[-1][:-3]


HUMAN_NOTES = "Human notes"
_FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:")


def _source_rules(pages: list[wiki_okf.Page],
                  objects: dict[str, str] | None) -> list[Finding]:
    out = []
    source_titles = {_norm(p.title): p.path for p in pages if p.type == "Source"}
    for p in pages:
        if p.type == "Source" and objects is not None:
            oid = str(p.extra.get("origin", ""))
            declared = str(p.extra.get("origin_hash", ""))
            if not oid:
                continue
            current = objects.get(oid)
            if current is None:
                out.append(Finding("src/missing", "error", p.path,
                                   f"origin object `{oid}` no longer exists"))
            elif declared and declared != current:
                out.append(Finding("src/hash-drift", "error", p.path,
                                   f"`{oid}` changed since ingest "
                                   f"({declared} -> {current})"))
        elif p.type in ("Concept", "Entity"):
            # The reference implementation's most common failure: the article
            # itself becomes a concept, and the wiki fills with pages named after
            # their sources instead of after ideas.
            other = source_titles.get(_norm(p.title))
            if other:
                out.append(Finding("src/is-concept", "error", p.path,
                                   f"title matches the source page {other} — a source "
                                   f"title is never a concept"))
    return out


def _trust_rules(pages: list[wiki_okf.Page],
                 previous: dict[str, str] | None) -> list[Finding]:
    out = []
    for p in pages:
        if p.status == "stable" and not p.verified:
            out.append(Finding("trust/unverified-stable", "info", p.path,
                               "status is stable but nothing has verified it"))
        if previous is None:
            continue
        before = previous.get(p.path)
        if before is None:
            continue
        had = wiki_okf.Page(path=p.path, body=before).has_section(HUMAN_NOTES)
        if had and not p.has_section(HUMAN_NOTES):
            out.append(Finding("human-notes/removed", "error", p.path,
                               "the protected `# Human notes` section was removed"))
        was_empty = not wiki_okf.Page(path=p.path, body=before).section(HUMAN_NOTES).strip()
        if was_empty and p.section(HUMAN_NOTES).strip():
            out.append(Finding("human-notes/machine-written", "error", p.path,
                               "content appeared under the protected `# Human notes`"))
    return out


def _human_notes_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    """Footnote definitions inside the protected section.

    Markdown convention puts `[^id]: ...` at the end of the document and the page
    template ends with `# Human notes`, so the agent's attributions land in the one
    section it may not write in. The reader then offers them to a human as if they
    were notes, and saving over them destroys the page's attributions. Unlike
    `human-notes/machine-written` this needs no previous revision, so it also finds
    the pages already written that way."""
    out = []
    for p in pages:
        notes = p.section(HUMAN_NOTES)
        if not notes.strip():
            continue
        bad = [ln for ln in notes.splitlines() if _FOOTNOTE_DEF.match(ln.strip())]
        if bad:
            out.append(Finding(
                "human-notes/footnote-definition", "error", p.path,
                f"{len(bad)} footnote definition(s) inside the protected "
                f"`# Human notes` — move them to `# References`"))
    return out


def run_lint(substrate, program_id: str, *, now: float | None = None,
             fix: bool = False) -> tuple[list[Finding], int]:
    """Lint a real bundle. The only function here that touches the filesystem;
    everything above it is pure so the rules can be tested without a substrate.

    With `fix=True` the mechanical fixes are applied and written back first, so
    the findings returned are what is actually left for a human or an agent."""
    from coscience import wiki_store
    pages = wiki_store.iter_pages(substrate, program_id)
    fixed_count = 0
    if fix:
        changed, _ = autofix(pages)
        for page in changed:
            wiki_store.write_page(substrate, program_id, page)
        fixed_count = len(changed)
    objects = {o.oid: wiki_store.object_hash(o)
               for o in wiki_store.program_objects(substrate, program_id)}
    try:
        index_body = (wiki_store.bundle_dir(substrate, program_id) / "index.md").read_text()
    except OSError:
        index_body = ""
    findings = lint(pages, index_body=index_body, objects=objects,
                    previous=wiki_store.previous_bodies(substrate, program_id),
                    now=now)
    return findings, fixed_count
