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
    linked = set(wiki_okf.body_links(index_body))
    for p in pages:
        linked.update(wiki_okf.body_links(p.body))
    by_slug: dict[str, list[str]] = {}
    titles: dict[str, str] = {}

    for p in pages:
        if len(p.body.strip()) < STUB_CHARS:
            out.append(Finding("page/stub", "warn", p.path,
                               f"body is under {STUB_CHARS} characters"))
        stale = _staleness(p, now)
        if stale:
            out.append(Finding("page/stale", "warn", p.path, stale))
        if not _is_linked(p.path, linked):
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


def _is_linked(path: str, linked: set[str]) -> bool:
    """Links are written bundle-absolute (/concepts/a.md) but may appear
    relative; accept either rather than crying orphan on a working link."""
    return any(target.lstrip("/").endswith(path) or target.endswith(path)
               for target in linked)


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
