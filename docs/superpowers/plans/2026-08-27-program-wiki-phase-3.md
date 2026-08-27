# Program Wiki Phase 3 (Lint Runs & Merges) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The wiki maintains itself — duplicate pages the agent finds actually get merged, under a per-program policy that is either fully automatic or human-approved, and a maintenance page shows you what every run did.

**Architecture:** A new pure module `wiki_merge.py` plans a merge of two parsed pages into one and returns the rewritten pages; `Service.merge_wiki_pages` does the IO and commits. The agent never merges and never deletes — it writes proposals into `report.json["merges"]`, and the only difference between the two policies is who authorises the apply: `wiki._collect` in `auto`, an HTTP call in `propose`. Mechanical merges stack prose, so the survivor carries a `merged_from` marker that raises a new lint rule until a later lint run rewrites it. A capped `state["runs"]` list is the audit trail, and `WikiLintView` renders it.

**Tech Stack:** Python 3.11+, FastAPI, `fastapi.testclient`, pytest. React 18, react-router-dom, `@tanstack/react-query`, Mantine, react-markdown via `components/Md.tsx`, vitest + `@testing-library/react`.

**Spec:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — §9.1 (merges) and §11.3 (maintenance UI) are the new binding sections; §8.3 (run state), §9 (the rule table) and §11.1 (endpoints) were amended for this phase. **Where this plan and the spec disagree, the spec wins.**

**Predecessors:** phases 1 and 2 are complete on `feat/program-wiki`. Read `docs/knowledge/NEXT.md` first — §1 lists what already exists and must not be rebuilt. Read the charter's §7 decision log before overturning anything; every ruling there was argued once already.

## Global Constraints

Every task's requirements implicitly include this section.

- **Two repos.** This repo is CODE. The wiki bundle lives in the **substrate**, a separate git repo at `$COSCIENCE_REPO`. Never confuse them.
- **`python` is not on PATH.** Run tests with `~/venvs/coscience/bin/python -m pytest`.
- **Frontend tests:** `cd frontend && npx vitest run`. Typecheck with `npx tsc --noEmit`.
- **The unit suite never calls a live LLM.** `wiki_agent` is injectable; tests pass `FakeWikiAgent` from `tests/test_wiki_beat.py`. A test that can reach `agent.launch` for real is wrong.
- **`@testing-library/user-event` is NOT a dependency.** Use `fireEvent`. Do not run `npm install`.
- **Scope frontend assertions.** Query by role, title, or a scoped element — never a bare `getByText` in a view full of counts. Every phase-2 test defect was this mistake.
- **Python ≥3.11**: `X | None`, `from __future__ import annotations` at the top of every new module.
- **The seam rule.** Logic modules are pure with no IO. All substrate writes happen in `Service` or the orchestrator `wiki.py`. `wiki_lint.run_lint` is the single documented exception.
- **Frozen vocabularies.** 12 relation types, 5 page types. Adding one is a spec change.
- **OKF conformance.** Parsers tolerate unknown types, unknown keys and broken links; they never raise and never drop unknown keys. `merged_from` rides in `Page.extra` for exactly this reason.
- **Never commit or push without the human's explicit approval.** Local commits on `feat/program-wiki` are pre-authorised for this plan's execution; that does not extend to pushing, merging or deploying.
- **Stage explicit paths, never `git add -A`.**

---

## File structure

| File | Responsibility |
|---|---|
| `src/coscience/wiki_merge.py` | **NEW, pure.** Plan a merge: given two parsed pages and the rest of the bundle, return the rewritten survivor and every other page whose links or relations changed. No IO. |
| `src/coscience/wiki_lint.py` | One new rule, `page/unmerged-prose`. |
| `src/coscience/wiki_prompts.py` | Pin the `merges` shape in `_HOUSEKEEPING`; add the prose pass to `render_lint`. |
| `src/coscience/wiki_store.py` | Three new `DEFAULT_STATE` keys; bundle `CLAUDE.md` template gains the `merges` shape. |
| `src/coscience/wiki.py` | `_collect` records a run in `state["runs"]` and, under `auto`, applies merges; under `propose`, queues them. |
| `src/coscience/models.py` | `Program.wiki_merge: str = "auto"`. |
| `src/coscience/service.py` | `merge_wiki_pages`, `list_wiki_merges`, `accept_wiki_merge`, `reject_wiki_merge`, `wiki_activity`, `set_program_wiki_merge`; `wiki_lint_report` gains filed reports. |
| `src/coscience/http_api.py` | Six endpoints per spec §11.1. |
| `frontend/src/api.ts` | Types and client methods for all six. |
| `frontend/src/views/WikiLintView.tsx` | **NEW.** The maintenance page: activity, reports, findings, proposals. |
| `frontend/src/views/WikiView.tsx` | In-page findings strip; merge-policy control in the header. |
| `frontend/src/components/ProgramSettingsModal.tsx` | Merge-policy select beside `wiki_model`. |
| `frontend/src/App.tsx` | The `/programs/:id/wiki/lint` route. |

**Task order matters.** Tasks 1–3 are pure and have no dependencies beyond the existing modules. Task 4 needs 1. Tasks 5–7 need 4. Tasks 8–9 need 5–7. Tasks 10–14 are frontend and need 8–9.

---

### Task 1: `wiki_merge.plan` — the pure merge

**Files:**
- Create: `src/coscience/wiki_merge.py`
- Test: `tests/test_wiki_merge.py`

**Interfaces:**
- Consumes: `wiki_okf.Page`, `wiki_okf.Relation`, `wiki_okf.Source` (existing dataclasses).
- Produces: `MergePlan` dataclass with fields `winner: Page`, `rewritten: list[Page]`, `loser_path: str`; and `plan(winner: Page, loser: Page, others: list[Page]) -> MergePlan`. `others` is every OTHER page in the bundle (excluding winner and loser). Later tasks rely on these exact names.

Spec §9.1's element table is the binding definition. Read it before writing the test.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import pytest

from coscience import wiki_merge, wiki_okf


def _page(path, title, body="", **kw):
    return wiki_okf.Page(path=path, type=kw.pop("type", "Concept"), title=title,
                         body=body, **kw)


def test_sections_are_stacked_under_the_winners_headings():
    winner = _page("concepts/a.md", "A", "# Definition\n\nA is a thing.\n")
    loser = _page("concepts/b.md", "B", "# Definition\n\nB is the same thing.\n")
    out = wiki_merge.plan(winner, loser, [])
    definition = out.winner.section("Definition")
    assert "A is a thing." in definition
    assert "B is the same thing." in definition


def test_a_section_only_the_loser_has_is_carried_over():
    winner = _page("concepts/a.md", "A", "# Definition\n\nA.\n")
    loser = _page("concepts/b.md", "B", "# Definition\n\nB.\n\n# Evidence\n\n- b1\n")
    out = wiki_merge.plan(winner, loser, [])
    assert "- b1" in out.winner.section("Evidence")


def test_human_notes_from_both_survive_and_say_where_they_came_from():
    """Spec 9.1: notes are the human's own words. Losing them is the one thing
    the protected section exists to prevent."""
    winner = _page("concepts/a.md", "A", "# Human notes\n\nkeep me\n")
    loser = _page("concepts/b.md", "B", "# Human notes\n\nkeep me too\n")
    out = wiki_merge.plan(winner, loser, [])
    notes = out.winner.section("Human notes")
    assert "keep me" in notes and "keep me too" in notes
    assert "b" in notes.lower()          # labelled with its origin page


def test_verification_is_cleared():
    """A verification is a claim about specific text, and the text just changed."""
    winner = _page("concepts/a.md", "A", verified=[{"by": "human:x", "at": 1.0}])
    loser = _page("concepts/b.md", "B", verified=[{"by": "human:y", "at": 2.0}])
    assert wiki_merge.plan(winner, loser, []).winner.verified == []


def test_relations_are_unioned_and_deduped():
    r = wiki_okf.Relation(type="part_of", target="/concepts/c.md", source="s1")
    winner = _page("concepts/a.md", "A", relations=[r])
    loser = _page("concepts/b.md", "B", relations=[
        wiki_okf.Relation(type="part_of", target="/concepts/c.md", source="s2"),
        wiki_okf.Relation(type="requires", target="/concepts/d.md", source="s3")])
    rels = wiki_merge.plan(winner, loser, []).winner.relations
    assert len(rels) == 2
    assert {(x.type, x.target) for x in rels} == {
        ("part_of", "/concepts/c.md"), ("requires", "/concepts/d.md")}


def test_a_relation_from_the_winner_to_the_loser_is_dropped():
    """After the merge the loser is the winner. A page cannot relate to itself."""
    winner = _page("concepts/a.md", "A", relations=[
        wiki_okf.Relation(type="refines", target="/concepts/b.md", source="s")])
    out = wiki_merge.plan(winner, _page("concepts/b.md", "B"), [])
    assert out.winner.relations == []


def test_sources_are_unioned_and_deduped_by_id():
    s = wiki_okf.Source(id="c1", resource="/sources/result-r1.md")
    winner = _page("concepts/a.md", "A", sources=[s])
    loser = _page("concepts/b.md", "B", sources=[
        wiki_okf.Source(id="c1", resource="/sources/result-r1.md"),
        wiki_okf.Source(id="c2", resource="/sources/result-r2.md")])
    assert [x.id for x in wiki_merge.plan(winner, loser, []).winner.sources] == ["c1", "c2"]


def test_the_losers_title_and_slug_become_aliases():
    """The old name must still find the page, or the merge loses discoverability."""
    winner = _page("concepts/a.md", "A", aliases=["ay"])
    loser = _page("concepts/b.md", "Bee", aliases=["bee-alias"])
    aliases = wiki_merge.plan(winner, loser, []).winner.aliases
    assert set(aliases) >= {"ay", "Bee", "b", "bee-alias"}
    assert len(aliases) == len(set(aliases))


def test_the_winner_is_marked_as_needing_a_prose_pass():
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [])
    assert out.winner.extra["merged_from"] == ["b"]


def test_merging_twice_accumulates_the_marker():
    winner = _page("concepts/a.md", "A", extra={"merged_from": ["b"]})
    out = wiki_merge.plan(winner, _page("concepts/c.md", "C"), [])
    assert out.winner.extra["merged_from"] == ["b", "c"]


def test_other_pages_have_their_links_rewritten():
    other = _page("concepts/z.md", "Z",
                  "See [B](/concepts/b.md) and [[b]] and [B again](concepts/b.md).")
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    body = out.rewritten[0].body
    assert "/concepts/b.md" not in body and "[[b]]" not in body
    assert body.count("/concepts/a.md") == 2 and "[[a]]" in body


def test_other_pages_have_their_relations_retargeted_not_dropped():
    """Spec 9.1: delete_wiki_page drops these. A merge must not — dropping them
    discards exactly the knowledge the merge exists to preserve."""
    other = _page("concepts/z.md", "Z", relations=[
        wiki_okf.Relation(type="requires", target="/concepts/b.md", source="s")])
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    assert [(r.type, r.target) for r in out.rewritten[0].relations] == [
        ("requires", "/concepts/a.md")]


def test_retargeting_that_creates_a_duplicate_relation_dedupes():
    other = _page("concepts/z.md", "Z", relations=[
        wiki_okf.Relation(type="requires", target="/concepts/a.md", source="s"),
        wiki_okf.Relation(type="requires", target="/concepts/b.md", source="s")])
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    assert len(out.rewritten[0].relations) == 1


def test_a_page_that_never_mentioned_the_loser_is_not_rewritten():
    """rewritten is what the caller must write back. Listing untouched pages
    would make every merge commit touch the whole bundle."""
    other = _page("concepts/z.md", "Z", "nothing to see")
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [other])
    assert out.rewritten == []


def test_the_loser_path_is_reported_for_the_caller_to_delete():
    out = wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/b.md", "B"), [])
    assert out.loser_path == "concepts/b.md"


def test_refuses_a_source_page():
    """Spec 9.1: a source page is bound to a real object by origin_hash. Merging
    two would make src/hash-drift and src/missing meaningless."""
    with pytest.raises(ValueError):
        wiki_merge.plan(_page("concepts/a.md", "A"),
                        _page("sources/result-r1.md", "R1", type="Source"), [])
    with pytest.raises(ValueError):
        wiki_merge.plan(_page("sources/result-r1.md", "R1", type="Source"),
                        _page("concepts/a.md", "A"), [])


def test_refuses_merging_a_page_into_itself():
    with pytest.raises(ValueError):
        wiki_merge.plan(_page("concepts/a.md", "A"), _page("concepts/a.md", "A"), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_merge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_merge'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Plan a merge of two wiki pages into one.

Pure — no IO. `plan` returns the rewritten pages; writing them, deleting the
loser and committing is the caller's job (Service.merge_wiki_pages).

Why the agent never does this itself: merging means deleting a page, which every
wiki prompt forbids and which would need its own trust argument. The agent
proposes; the platform performs. Spec 9.1.
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

    Raises ValueError for a merge the wiki must never perform."""
    if winner.path == loser.path:
        raise ValueError("cannot merge a page into itself")
    if "Source" in (winner.type, loser.type):
        raise ValueError("source pages are bound to a real object and never merge")

    merged = wiki_okf.Page(**{**winner.__dict__})
    merged.relations = list(winner.relations)
    merged.sources = list(winner.sources)
    merged.aliases = list(winner.aliases)
    merged.extra = dict(winner.extra)

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
        rels = [r for r in page.relations]
        touched_rel = any(_rel_key(r)[1] == loser.path for r in rels)
        if touched_rel:
            for r in rels:
                if _rel_key(r)[1] == loser.path:
                    r.target = f"/{winner.path}"
            rels = _dedupe(rels)
        if body == page.body and not touched_rel:
            continue
        page.body = body
        page.relations = rels
        rewritten.append(page)

    return MergePlan(winner=merged, rewritten=rewritten, loser_path=loser.path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_merge.py -q`
Expected: PASS, 16 tests.

- [ ] **Step 5: Run the whole wiki suite** — nothing in phases 1–2 may change.

Run: `~/venvs/coscience/bin/python -m pytest tests/ -k wiki -q`
Expected: PASS.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_merge.py tests/test_wiki_merge.py
git commit -m "feat(wiki): wiki_merge — plan a merge of two pages into one"
```

---

### Task 2: `page/unmerged-prose` — the rule that gets the prose fixed

**Files:**
- Modify: `src/coscience/wiki_lint.py`
- Test: `tests/test_wiki_lint_merge.py`

**Interfaces:**
- Consumes: `wiki_okf.Page.extra["merged_from"]`, set by Task 1.
- Produces: a `Finding(rule="page/unmerged-prose", severity="warn", path=<page path>)`. Task 12 renders it.

Read the top of `wiki_lint.py` for how existing rules are structured before writing. This rule is **not auto-fixable** — only an agent can rewrite prose.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from coscience import wiki_lint, wiki_okf


def _page(path, title, **kw):
    return wiki_okf.Page(path=path, type="Concept", title=title,
                         body="# Definition\n\n" + "x" * 300, **kw)


def _rules(findings):
    return [f.rule for f in findings]


def test_a_merged_page_is_flagged_until_its_prose_is_rewritten():
    pages = [_page("concepts/a.md", "A", extra={"merged_from": ["b"]})]
    findings = wiki_lint.lint(pages)
    assert "page/unmerged-prose" in _rules(findings)


def test_the_finding_names_what_was_merged_in():
    pages = [_page("concepts/a.md", "A", extra={"merged_from": ["b", "c"]})]
    found = [f for f in wiki_lint.lint(pages) if f.rule == "page/unmerged-prose"]
    assert "b" in found[0].message and "c" in found[0].message


def test_a_page_with_no_marker_is_not_flagged():
    assert "page/unmerged-prose" not in _rules(wiki_lint.lint([
        _page("concepts/a.md", "A")]))


def test_an_empty_marker_is_not_flagged():
    """Clearing the marker is how an agent says the prose pass is done."""
    assert "page/unmerged-prose" not in _rules(wiki_lint.lint([
        _page("concepts/a.md", "A", extra={"merged_from": []})]))


def test_a_malformed_marker_does_not_raise():
    """OKF parsers never raise. An agent writing a string here is a bad page,
    not a crashed lint run."""
    findings = wiki_lint.lint([
        _page("concepts/a.md", "A", extra={"merged_from": "b"})])
    assert "page/unmerged-prose" in _rules(findings)


def test_the_rule_is_a_warning_not_an_error():
    """Stacked prose is ugly, not broken. An error here would make every merge
    fail the health badge until an agent happened to run."""
    found = [f for f in wiki_lint.lint([
        _page("concepts/a.md", "A", extra={"merged_from": ["b"]})])
        if f.rule == "page/unmerged-prose"]
    assert found[0].severity == "warn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_lint_merge.py -q`
Expected: FAIL — no `page/unmerged-prose` finding.

- [ ] **Step 3: Write minimal implementation**

`wiki_lint.lint(pages, *, index_body="", objects=None, previous=None, now=None)` is
the pure entry point; the per-page rules live in `_page_rules`, in the `for p in
pages:` loop that already emits `page/stub` and `page/stale`. Add there:

```python
        marker = p.extra.get("merged_from")
        names = ([str(x) for x in marker] if isinstance(marker, list)
                 else [str(marker)] if marker else [])
        if names:
            out.append(Finding("page/unmerged-prose", "warn", p.path,
                               "merged from " + ", ".join(names)
                               + " — sections are still two pages stacked"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_lint_merge.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the whole lint suite**

Run: `~/venvs/coscience/bin/python -m pytest tests/ -k lint -q`
Expected: PASS. A pre-existing test asserting an exact finding count will need its number raised — that is expected and legitimate; a test asserting *behaviour* that now fails is not, and means the rule is wrong.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_lint.py tests/test_wiki_lint_merge.py
git commit -m "feat(wiki): lint flags a merged page until its prose is rewritten"
```

---

### Task 3: Pin the `merges` shape and add the prose pass to the prompts

**Files:**
- Modify: `src/coscience/wiki_prompts.py` (`_HOUSEKEEPING`, `render_lint`)
- Modify: `src/coscience/wiki_store.py` (the bundle `CLAUDE.md` template)
- Test: `tests/test_wiki_prompts_merge.py`

**Interfaces:**
- Produces: the `report.json["merges"]` contract that Task 5 parses — a list of `{"winner": <path>, "loser": <path>, "why": <text>}`.

Spec §9.1: *"An undefined key with no consumer is what `objects` was before the phase-1 review; the agent cannot honour a contract that was never written down."* Three places currently tell the agent to write `merges` without saying what it looks like: `wiki_store.py:104`, `wiki_prompts.py:102` and `:211`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from pathlib import Path

from coscience import wiki_prompts, wiki_store
from coscience.models import Program


def _doc():
    return wiki_prompts.render_lint(
        Program(id="p1", title="P1", goals="g"), Path("/b"), "report text",
        Path("/run"))


def test_the_lint_document_defines_the_merges_shape():
    doc = _doc()
    assert '"winner"' in doc and '"loser"' in doc and '"why"' in doc


def test_the_merges_shape_uses_page_paths_not_slugs():
    """A slug is ambiguous across directories; page/duplicate-slug exists because
    of it. The consumer resolves paths."""
    assert "concepts/" in _doc().split('"merges"', 1)[1][:400]


def test_the_lint_document_asks_for_the_prose_pass():
    doc = _doc()
    assert "merged_from" in doc
    assert "unmerged-prose" in doc or "one voice" in doc


def test_the_ingest_document_also_defines_merges():
    """A lint run is not the only run that can notice a duplicate."""
    doc = wiki_prompts.render_ingest(
        Program(id="p1", title="P1", goals="g"), Path("/b"), [], Path("/run"))
    assert '"merges"' in doc


def test_the_bundle_template_defines_merges_too(substrate):
    """The bundle's own CLAUDE.md is binding on the agent and is what it reads
    when the instruction document is not in front of it."""
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    text = (wiki_store.bundle_dir(substrate, "p1") / "CLAUDE.md").read_text()
    assert '"winner"' in text and '"loser"' in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_prompts_merge.py -q`
Expected: FAIL — the shape is nowhere.

- [ ] **Step 3: Write minimal implementation**

In `_HOUSEKEEPING`, extend the `report.json` block and add the paragraph after `objects`:

```python
```json
{{"pages_created": ["concepts/a.md"], "pages_updated": ["concepts/b.md"],
  "objects": ["result:r1"], "merges": [], "notes": "one or two sentences"}}
```
```

and after the `objects` paragraph:

```
   `merges` is how you report two pages that are the same idea. You never merge
   them yourself and you never delete a page — the platform performs the merge,
   either immediately or after a human approves it. Each entry is:

   {{"winner": "concepts/compute-lease.md", "loser": "concepts/job-lease.md",
     "why": "one paragraph: why these are the same idea"}}

   Both are bundle-relative page paths, not slugs. Never propose a `sources/`
   page: a source page stands for one real object and is bound to it. If you
   have nothing to propose, write `[]`.
```

In `render_lint`, replace step 4 with:

```
4. **Propose merges for near-duplicates.** Do not merge and do not delete — write
   the proposal into `report.json` under `"merges"` in the shape given below, and
   add an alias so the pages are at least findable as one idea.
5. **Rewrite merged pages.** A page whose frontmatter carries `merged_from` was
   merged mechanically: its sections are two pages stacked under one heading.
   Rewrite them into one voice — one definition, one set of evidence, no
   repetition — then delete the `merged_from` key. This is the only way that
   marker ever clears, and `page/unmerged-prose` reports it until you do.
```

Renumber the remaining steps. Apply the same `merges` shape to `wiki_store.py`'s bundle `CLAUDE.md` template at the "Never delete a page" line.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_prompts_merge.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the prompt suite**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_prompts.py tests/test_wiki_store.py -q`
Expected: PASS.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_prompts.py src/coscience/wiki_store.py tests/test_wiki_prompts_merge.py
git commit -m "feat(wiki): give report.json's merges key a shape and a prose pass"
```

---

### Task 4: `Service.merge_wiki_pages` — the IO half

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_wiki_merge.py`

**Interfaces:**
- Consumes: `wiki_merge.plan` (Task 1); existing `Service._wiki_pages`, `wiki_page_path`, `wiki_store.write_page`, `substrate.commit`.
- Produces: `Service.merge_wiki_pages(program_id: str, winner: str, loser: str) -> dict` where `winner`/`loser` are **bundle-relative paths** (`concepts/a.md`). Returns `{"winner": <path>, "loser": <path>, "rewritten": [<paths>]}`. Raises `NotFoundError` if either page is missing, `ValueError` for a refused merge. Tasks 5, 6 and 8 call it.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import pytest

from coscience import wiki_okf, wiki_store
from coscience.models import Program
from coscience.service import NotFoundError, Service


def _seed(substrate):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title, body in (
            ("concepts/a.md", "A", "# Definition\n\nA is a thing.\n"),
            ("concepts/b.md", "B", "# Definition\n\nB is the same thing.\n"),
            ("concepts/z.md", "Z", "See [B](/concepts/b.md).\n")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title, body=body))


def test_the_merge_writes_the_winner_and_removes_the_loser(substrate):
    _seed(substrate)
    out = Service(substrate.repo_root).merge_wiki_pages(
        "p1", "concepts/a.md", "concepts/b.md")
    assert out["loser"] == "concepts/b.md"
    winner = wiki_store.read_page(substrate, "p1", "concepts/a.md")
    assert "B is the same thing." in winner.section("Definition")
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is None


def test_pages_pointing_at_the_loser_are_rewritten(substrate):
    _seed(substrate)
    out = Service(substrate.repo_root).merge_wiki_pages(
        "p1", "concepts/a.md", "concepts/b.md")
    assert out["rewritten"] == ["concepts/z.md"]
    assert "/concepts/a.md" in wiki_store.read_page(substrate, "p1", "concepts/z.md").body


def test_the_merge_is_its_own_commit_naming_both_pages(substrate):
    """Spec 9.1 rules that nothing gates an automatic merge but git. That makes
    this commit the undo, so it has to be findable and it has to stand alone."""
    import subprocess
    _seed(substrate)
    Service(substrate.repo_root).merge_wiki_pages("p1", "concepts/a.md", "concepts/b.md")
    subject = subprocess.run(["git", "log", "-1", "--format=%s"],
                             cwd=substrate.repo_root, capture_output=True,
                             text=True).stdout.strip()
    assert "merge" in subject.lower()
    assert "concepts/a.md" in subject and "concepts/b.md" in subject


def test_a_missing_page_is_not_found(substrate):
    _seed(substrate)
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).merge_wiki_pages(
            "p1", "concepts/a.md", "concepts/ghost.md")


def test_a_source_page_is_refused(substrate):
    _seed(substrate)
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1"))
    with pytest.raises(ValueError):
        Service(substrate.repo_root).merge_wiki_pages(
            "p1", "concepts/a.md", "sources/result-r1.md")


def test_a_traversing_path_is_not_found(substrate):
    """Same containment guard as every other {slug:path} route. A page path
    arrives from a URL and is a traversal primitive."""
    _seed(substrate)
    with pytest.raises(NotFoundError):
        Service(substrate.repo_root).merge_wiki_pages(
            "p1", "concepts/a.md", "../../../etc/passwd")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_merge.py -q`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'merge_wiki_pages'`

- [ ] **Step 3: Write minimal implementation**

`Substrate` exposes `commit(message)` and no general `git()` helper — do not add
one for a test's convenience; the test above shells out directly.

Add to `Service`, beside `delete_wiki_page`:

```python
    def merge_wiki_pages(self, program_id: str, winner: str, loser: str) -> dict:
        """Fold `loser` into `winner`, rewrite everything that pointed at it, and
        delete it — in one commit.

        Deliberately NOT built on delete_wiki_page: that drops inbound relations,
        which is right for a deletion and wrong here. A merge retargets them, or
        it discards exactly the knowledge it exists to preserve (spec 9.1).

        Nothing gates this in `auto` but git, so the commit is the undo and names
        both pages."""
        from coscience import wiki_merge, wiki_store
        win = self._load_wiki_page(program_id, _strip_md(winner))
        lose = self._load_wiki_page(program_id, _strip_md(loser))
        others = [p for p in self._wiki_pages(program_id)
                  if p.path not in (win.path, lose.path)]
        result = wiki_merge.plan(win, lose, others)      # ValueError on a refusal

        wiki_store.write_page(self.substrate, program_id, result.winner)
        for page in result.rewritten:
            wiki_store.write_page(self.substrate, program_id, page)
        self.wiki_page_path(program_id, _strip_md(loser)).unlink()
        self.substrate.commit(
            f"wiki {program_id}: merged {result.loser_path} into {result.winner.path}")
        return {"winner": result.winner.path, "loser": result.loser_path,
                "rewritten": [p.path for p in result.rewritten]}
```

and at module level, beside `_replace_section`:

```python
def _strip_md(path: str) -> str:
    """Page paths cross the API as `concepts/a.md`; the guarded loaders take a
    slug without the extension. One place to convert, so the guard is never
    accidentally bypassed by a caller that forgot."""
    return path[:-3] if path.endswith(".md") else path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_merge.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_merge.py
git commit -m "feat(wiki): Service.merge_wiki_pages — perform a merge and commit it"
```

---

### Task 5: State fields, the run audit trail, and auto-apply at collect

**Files:**
- Modify: `src/coscience/wiki_store.py` (`DEFAULT_STATE`)
- Modify: `src/coscience/models.py` (`Program.wiki_merge`)
- Modify: `src/coscience/wiki.py` (`_collect`)
- Test: `tests/test_wiki_merge_beat.py`

**Interfaces:**
- Consumes: `Service.merge_wiki_pages` (Task 4).
- Produces: `state["runs"]` (capped list, newest first), `state["merge_proposals"]` (each with `id`, `winner`, `loser`, `why`, `run`, `at`), `state["merges_refused"]` (list of sorted `[path, path]` pairs), and `Program.wiki_merge`. Tasks 6–9 read all four.

Spec §8.3 defines the shapes. `RUNS_KEPT = 50`.

`_collect` currently calls `substrate.commit(...)` at the end. Merges must be applied **before** that commit is reached, each committing separately — a merge's commit is its undo and must not be entangled with the run's.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from coscience import wiki, wiki_okf, wiki_store
from coscience.models import Program
from tests.test_wiki_beat import FakeWikiAgent


def _bundle(substrate, policy="auto"):
    substrate.save_program(Program(id="p1", title="P1", goals="g", wiki_merge=policy))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title in (("concepts/a.md", "A"), ("concepts/b.md", "B")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title,
            body="# Definition\n\n" + "x" * 300))
    return substrate.load_program("p1")


def _finish(substrate, program, agent, report):
    """Launch a run, then hand back the report the agent 'wrote'."""
    wiki.beat(substrate, program, 1.0, agent, usage_gate=lambda: True)
    run_dir = wiki_store.run_dir(
        substrate, "p1", wiki_store.load_state(substrate, "p1")["run"]["id"])
    import json
    (run_dir / "report.json").write_text(json.dumps(report))
    (run_dir / "agent.exit").write_text("0")
    agent.alive = False
    return wiki.beat(substrate, program, 2.0, agent, usage_gate=lambda: True)


_MERGE = {"pages_created": [], "pages_updated": [], "objects": [],
          "merges": [{"winner": "concepts/a.md", "loser": "concepts/b.md",
                      "why": "same idea"}]}


def test_auto_applies_the_merge_at_collect(substrate):
    program = _bundle(substrate, "auto")
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is None
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == []


def test_propose_queues_it_instead(substrate):
    program = _bundle(substrate, "propose")
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is not None
    queued = wiki_store.load_state(substrate, "p1")["merge_proposals"]
    assert len(queued) == 1
    assert queued[0]["winner"] == "concepts/a.md" and queued[0]["id"]


def test_a_refused_pair_is_never_re_proposed(substrate):
    """Without this, every lint run re-offers the same merge and the human's
    'no' is worth nothing."""
    program = _bundle(substrate, "propose")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merges_refused"] = [["concepts/a.md", "concepts/b.md"]]
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == []


def test_a_refused_pair_is_matched_in_either_direction(substrate):
    program = _bundle(substrate, "propose")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merges_refused"] = [["concepts/b.md", "concepts/a.md"]]
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    assert wiki_store.load_state(substrate, "p1")["merge_proposals"] == []


def test_a_malformed_merges_entry_is_ignored_not_raised(substrate):
    """An exit-0 run is done. A bad report is a bad page, never a crashed beat."""
    program = _bundle(substrate, "auto")
    _finish(substrate, program, FakeWikiAgent(),
            {"merges": ["not a dict", {"winner": "concepts/a.md"}, {}]})
    assert wiki_store.read_page(substrate, "p1", "concepts/a.md") is not None


def test_a_refused_merge_is_recorded_so_it_stops_being_offered(substrate):
    """A source-page proposal can never succeed. Retrying it every run is waste."""
    program = _bundle(substrate, "auto")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1"))
    _finish(substrate, program, FakeWikiAgent(),
            {"merges": [{"winner": "concepts/a.md", "loser": "sources/result-r1.md",
                         "why": "no"}]})
    refused = wiki_store.load_state(substrate, "p1")["merges_refused"]
    assert ["concepts/a.md", "sources/result-r1.md"] in [sorted(p) for p in refused]


def test_every_run_is_recorded_in_the_audit_trail(substrate):
    program = _bundle(substrate, "auto")
    _finish(substrate, program, FakeWikiAgent(), _MERGE)
    runs = wiki_store.load_state(substrate, "p1")["runs"]
    assert runs[0]["kind"] and runs[0]["status"] == "ok"
    assert runs[0]["merged"] == [["concepts/b.md", "concepts/a.md"]]


def test_the_audit_trail_is_newest_first_and_capped(substrate):
    program = _bundle(substrate, "auto")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs"] = [{"id": f"r{i}"} for i in range(wiki.RUNS_KEPT + 5)]
    _finish(substrate, program, FakeWikiAgent(), {"merges": []})
    runs = wiki_store.load_state(substrate, "p1")["runs"]
    assert len(runs) == wiki.RUNS_KEPT
    assert runs[1]["id"] == "r0"          # the new one is at the front


def test_a_failed_run_is_still_recorded(substrate):
    """The audit trail answers 'what has this thing been doing'. A run that
    failed is part of the answer."""
    program = _bundle(substrate, "auto")
    wiki.beat(substrate, program, 1.0, FakeWikiAgent(), usage_gate=lambda: True)
    run_dir = wiki_store.run_dir(
        substrate, "p1", wiki_store.load_state(substrate, "p1")["run"]["id"])
    (run_dir / "agent.exit").write_text("1")
    agent = FakeWikiAgent()
    agent.alive = False
    wiki.beat(substrate, program, 2.0, agent, usage_gate=lambda: True)
    assert wiki_store.load_state(substrate, "p1")["runs"][0]["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_merge_beat.py -q`
Expected: FAIL — `Program` has no `wiki_merge`, and `state` has no `runs`.

- [ ] **Step 3: Write minimal implementation**

`models.py`, beside `wiki_enabled`:

```python
    wiki_merge: str = "auto"           # auto = merge duplicates unattended; propose = queue for a human
```

`wiki_store.py`:

```python
DEFAULT_STATE: dict = {"ingested": {}, "ingests_since_lint": 0, "run": None,
                       "last_run": None, "failures": 0, "quarantined": [],
                       "merge_proposals": [], "merges_refused": [], "runs": []}
```

`wiki.py`, module level:

```python
RUNS_KEPT = 50


def _pair(winner: str, loser: str) -> list[str]:
    return sorted([winner, loser])


def _proposals(report: dict) -> list[tuple[str, str, str]]:
    """(winner, loser, why) from a report, tolerating anything. An exit-0 run is
    done; a malformed report is a bad page, never a crashed beat."""
    out = []
    for entry in (report.get("merges") or []):
        if not isinstance(entry, dict):
            continue
        winner, loser = str(entry.get("winner") or ""), str(entry.get("loser") or "")
        if winner and loser:
            out.append((winner, loser, str(entry.get("why") or "")))
    return out


def _next_merge_id(state: dict) -> str:
    n = 0
    for p in (state.get("merge_proposals") or []):
        try:
            n = max(n, int(str(p.get("id", "m0")).lstrip("m")))
        except ValueError:
            pass
    return f"m{n + 1:04d}"


def _handle_merges(substrate, program, state, report, run_id, now) -> list[list[str]]:
    """Apply or queue what the agent proposed. Returns the pairs actually merged.

    Which of the two happens is the ONLY difference between the policies —
    the agent's instructions and prohibitions are identical either way (spec 9.1)."""
    from coscience.service import Service
    refused = {tuple(sorted(p)) for p in (state.get("merges_refused") or [])
               if isinstance(p, list) and len(p) == 2}
    merged: list[list[str]] = []
    service = None
    for winner, loser, why in _proposals(report):
        pair = _pair(winner, loser)
        if tuple(pair) in refused:
            continue                       # a human already said no
        if getattr(program, "wiki_merge", "auto") != "auto":
            state.setdefault("merge_proposals", []).append(
                {"id": _next_merge_id(state), "winner": winner, "loser": loser,
                 "why": why, "run": run_id, "at": now})
            refused.add(tuple(pair))       # do not queue the same pair twice
            continue
        service = service or Service(substrate.repo_root)
        try:
            service.merge_wiki_pages(program.id, winner, loser)
        except Exception:                  # NotFoundError, ValueError, or a bad path
            state.setdefault("merges_refused", []).append(pair)
            refused.add(tuple(pair))
            continue
        merged.append([loser, winner])
    return merged
```

In `_collect`, after `state["last_run"] = {...}` and before the escape branch's early return, add nothing; then in the `status == "ok"` branch, after the ingest bookkeeping:

```python
        merged = _handle_merges(substrate, program, state, report, run_id, now)
```

and immediately before the final `substrate.commit(...)`:

```python
    state["runs"] = ([{"id": run_id, "kind": kind, "status": state["last_run"]["status"],
                       "at": now,
                       "pages_created": state["last_run"]["pages_created"],
                       "pages_updated": state["last_run"]["pages_updated"],
                       "merged": merged}]
                     + list(state.get("runs") or []))[:RUNS_KEPT]
```

Initialise `merged: list[list[str]] = []` at the top of `_collect` so the failed and escaped paths record a run too. The escaped branch returns early — add the same `state["runs"]` prepend before its `return`.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_wiki_merge_beat.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Run the whole wiki suite**

Run: `~/venvs/coscience/bin/python -m pytest tests/ -k wiki -q`
Expected: PASS. `test_wiki_state.py` may assert `DEFAULT_STATE`'s exact keys — update it to include the three new ones.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/wiki.py src/coscience/wiki_store.py src/coscience/models.py tests/test_wiki_merge_beat.py tests/test_wiki_state.py
git commit -m "feat(wiki): apply or queue merges at collect, and record every run"
```

---

### Task 6: `Service` — proposals, accept, reject, activity, policy

**Files:**
- Modify: `src/coscience/service.py`
- Test: `tests/test_service_wiki_proposals.py`

**Interfaces:**
- Consumes: `Service.merge_wiki_pages` (Task 4), the state keys from Task 5.
- Produces:
  - `list_wiki_merges(program_id) -> list[dict]`
  - `accept_wiki_merge(program_id, merge_id) -> dict` — `{"applied": bool, "winner", "loser", "rewritten"}`; `NotFoundError` for an unknown id
  - `reject_wiki_merge(program_id, merge_id) -> dict` — `{"rejected": [winner, loser]}`
  - `wiki_activity(program_id) -> list[dict]`
  - `set_program_wiki_merge(program_id, policy) -> dict` — `ValueError` outside `auto|propose`

  Tasks 8 and 10 call all five.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import pytest

from coscience import wiki_okf, wiki_store
from coscience.models import Program
from coscience.service import NotFoundError, Service


def _seed(substrate, proposals=True):
    substrate.save_program(Program(id="p1", title="P1", goals="g", wiki_merge="propose"))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title in (("concepts/a.md", "A"), ("concepts/b.md", "B")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title, body="# Definition\n\nx\n"))
    if proposals:
        with wiki_store.state_guard(substrate, "p1") as state:
            state["merge_proposals"] = [
                {"id": "m0001", "winner": "concepts/a.md", "loser": "concepts/b.md",
                 "why": "same idea", "run": "r0001", "at": 1.0}]
    return Service(substrate.repo_root)


def test_proposals_are_listed_with_the_agents_reasoning(substrate):
    rows = _seed(substrate).list_wiki_merges("p1")
    assert rows[0]["id"] == "m0001" and rows[0]["why"] == "same idea"


def test_accepting_applies_the_merge_now(substrate):
    """Spec 9.1: the wiki is never left in a state where a human approved
    something and nothing happened."""
    svc = _seed(substrate)
    out = svc.accept_wiki_merge("p1", "m0001")
    assert out["applied"] is True
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is None
    assert svc.list_wiki_merges("p1") == []


def test_accepting_a_stale_proposal_drops_it_without_an_error(substrate):
    """A proposal about a wiki that no longer exists is not an error worth
    surfacing to a human who did nothing wrong."""
    svc = _seed(substrate)
    svc.wiki_page_path("p1", "concepts/b").unlink()
    out = svc.accept_wiki_merge("p1", "m0001")
    assert out["applied"] is False
    assert svc.list_wiki_merges("p1") == []


def test_accepting_an_unknown_id_is_not_found(substrate):
    with pytest.raises(NotFoundError):
        _seed(substrate).accept_wiki_merge("p1", "m9999")


def test_rejecting_remembers_the_pair(substrate):
    svc = _seed(substrate)
    svc.reject_wiki_merge("p1", "m0001")
    assert svc.list_wiki_merges("p1") == []
    refused = wiki_store.load_state(substrate, "p1")["merges_refused"]
    assert sorted(refused[0]) == ["concepts/a.md", "concepts/b.md"]
    assert wiki_store.read_page(substrate, "p1", "concepts/b.md") is not None


def test_activity_is_the_recorded_runs(substrate):
    svc = _seed(substrate, proposals=False)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["runs"] = [{"id": "r0002", "kind": "lint", "status": "ok", "at": 2.0,
                          "merged": [["concepts/b.md", "concepts/a.md"]]}]
    assert svc.wiki_activity("p1")[0]["id"] == "r0002"


def test_the_policy_can_be_set_and_rejects_anything_else(substrate):
    svc = _seed(substrate, proposals=False)
    assert svc.set_program_wiki_merge("p1", "auto")["wiki_merge"] == "auto"
    assert substrate.load_program("p1").wiki_merge == "auto"
    with pytest.raises(ValueError):
        svc.set_program_wiki_merge("p1", "sometimes")


def test_setting_the_policy_on_a_missing_program_is_not_found(substrate):
    with pytest.raises(NotFoundError):
        _seed(substrate, proposals=False).set_program_wiki_merge("ghost", "auto")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_proposals.py -q`
Expected: FAIL — no `list_wiki_merges`.

- [ ] **Step 3: Write minimal implementation**

```python
    _WIKI_MERGE_POLICIES = ("auto", "propose")

    def list_wiki_merges(self, program_id: str) -> list[dict]:
        from coscience import wiki_store
        state = wiki_store.load_state(self.substrate, program_id)
        return list(state.get("merge_proposals") or [])

    def _take_proposal(self, state: dict, merge_id: str) -> dict:
        pending = list(state.get("merge_proposals") or [])
        for i, entry in enumerate(pending):
            if str(entry.get("id")) == merge_id:
                state["merge_proposals"] = pending[:i] + pending[i + 1:]
                return entry
        raise NotFoundError(merge_id)

    def accept_wiki_merge(self, program_id: str, merge_id: str) -> dict:
        """Apply a queued merge now.

        Now, rather than at the next beat: a human who clicked accept and saw
        nothing happen has no way to tell approval from a bug (spec 9.1). Prose
        is tidied later by the lint run the merged_from marker summons."""
        from coscience import wiki_store
        with wiki_store.state_guard(self.substrate, program_id) as state:
            entry = self._take_proposal(state, merge_id)
        winner, loser = entry["winner"], entry["loser"]
        try:
            out = self.merge_wiki_pages(program_id, winner, loser)
        except (NotFoundError, ValueError):
            # Re-checked at apply time on purpose: pages move between an agent
            # proposing and a human clicking.
            with wiki_store.state_guard(self.substrate, program_id) as state:
                state.setdefault("merges_refused", []).append(sorted([winner, loser]))
            return {"applied": False, "winner": winner, "loser": loser, "rewritten": []}
        return {"applied": True, **out}

    def reject_wiki_merge(self, program_id: str, merge_id: str) -> dict:
        from coscience import wiki_store
        with wiki_store.state_guard(self.substrate, program_id) as state:
            entry = self._take_proposal(state, merge_id)
            pair = sorted([entry["winner"], entry["loser"]])
            state.setdefault("merges_refused", []).append(pair)
        return {"rejected": pair}

    def wiki_activity(self, program_id: str) -> list[dict]:
        from coscience import wiki_store
        return list(wiki_store.load_state(self.substrate, program_id).get("runs") or [])

    def set_program_wiki_merge(self, program_id: str, policy: str) -> dict:
        """auto merges duplicates unattended; propose queues them for a human.

        Nothing gates auto but git — each merge is its own commit and that commit
        is the undo. Ruled deliberately (spec 9.1)."""
        if policy not in self._WIKI_MERGE_POLICIES:
            raise ValueError(f"policy must be auto or propose: {policy}")
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        program = self.substrate.load_program(program_id)
        program.wiki_merge = policy
        self.substrate.save_program(program)
        return {"id": program_id, "wiki_merge": policy}
```

Also add `wiki_merge` and the pending count to `wiki_summary`'s return, beside `wiki_model` / `wiki_enabled`:

```python
                "wiki_merge": getattr(program, "wiki_merge", "auto"),
                "merge_proposals": len(state.get("merge_proposals") or []),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_proposals.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_proposals.py
git commit -m "feat(wiki): Service methods for merge proposals, activity and policy"
```

---

### Task 7: `/wiki/lint` serves the agent's filed reports too

**Files:**
- Modify: `src/coscience/service.py` (`wiki_lint_report`)
- Test: `tests/test_service_wiki_reports.py`

**Interfaces:**
- Produces: `wiki_lint_report` gains a `reports` key — `[{"date": "2026-08-27", "text": "..."}]`, newest first. Task 12 renders it. `counts` and `findings` keep their existing shapes; Task 12 and the existing header badge both depend on them.

Spec §11.1: *"`/wiki/lint` serves both halves … Only the first half existed after phase 2."* `wiki._file_lint_report` writes these to `.wiki/lint/<date>.md`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from coscience import wiki_store
from coscience.models import Program
from coscience.service import Service


def _seed(substrate, days=()):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    d = wiki_store.state_dir(substrate, "p1") / "lint"
    d.mkdir(parents=True, exist_ok=True)
    for day in days:
        (d / f"{day}.md").write_text(f"# lint {day}\n")
    return Service(substrate.repo_root)


def test_filed_reports_come_back_newest_first(substrate):
    out = _seed(substrate, ["2026-08-20", "2026-08-27", "2026-08-24"]).wiki_lint_report("p1")
    assert [r["date"] for r in out["reports"]] == ["2026-08-27", "2026-08-24", "2026-08-20"]
    assert "# lint 2026-08-27" in out["reports"][0]["text"]


def test_a_bundle_with_no_reports_returns_an_empty_list(substrate):
    assert _seed(substrate).wiki_lint_report("p1")["reports"] == []


def test_live_findings_are_still_served(substrate):
    """The header badge reads counts. Adding reports must not move it."""
    out = _seed(substrate, ["2026-08-27"]).wiki_lint_report("p1")
    assert "counts" in out and "findings" in out


def test_a_non_markdown_file_in_the_lint_directory_is_ignored(substrate):
    svc = _seed(substrate, ["2026-08-27"])
    (wiki_store.state_dir(substrate, "p1") / "lint" / "notes.txt").write_text("x")
    assert [r["date"] for r in svc.wiki_lint_report("p1")["reports"]] == ["2026-08-27"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_reports.py -q`
Expected: FAIL — `KeyError: 'reports'`

- [ ] **Step 3: Write minimal implementation**

In `wiki_lint_report`, before the return:

```python
        from coscience import wiki_store
        d = wiki_store.state_dir(self.substrate, program_id) / "lint"
        reports = []
        try:
            files = sorted(d.glob("*.md"), key=lambda f: f.stem, reverse=True)
        except OSError:
            files = []
        for f in files:
            try:
                reports.append({"date": f.stem, "text": f.read_text()})
            except OSError:
                continue
```

and add `"reports": reports` to the returned dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_service_wiki_reports.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/service.py tests/test_service_wiki_reports.py
git commit -m "feat(wiki): serve the agent's filed lint reports alongside live findings"
```

---

### Task 8: HTTP endpoints

**Files:**
- Modify: `src/coscience/http_api.py`
- Test: `tests/test_http_wiki_merge.py`

**Interfaces:**
- Consumes: every `Service` method from Tasks 4, 6 and 7.
- Produces: the six routes in spec §11.1. Tasks 10–13 call them.

Register the new GETs **before** the existing `/wiki/pages/{slug:path}` route — a literal path must not be swallowed by the catch-all. Read the comment at `http_api.py:680` before touching the order.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from coscience import wiki_okf, wiki_store
from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate, policy="propose"):
    substrate.save_program(Program(id="p1", title="P1", goals="g", wiki_merge=policy))
    wiki_store.ensure_bundle(substrate, "p1")
    for path, title in (("concepts/a.md", "A"), ("concepts/b.md", "B")):
        wiki_store.write_page(substrate, "p1", wiki_okf.Page(
            path=path, type="Concept", title=title, body="# Definition\n\nx\n"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["merge_proposals"] = [
            {"id": "m0001", "winner": "concepts/a.md", "loser": "concepts/b.md",
             "why": "same idea", "run": "r0001", "at": 1.0}]
        state["runs"] = [{"id": "r0001", "kind": "lint", "status": "ok", "at": 1.0,
                          "merged": []}]


def test_proposals_are_listed(substrate):
    _seed(substrate)
    body = _client(substrate).get("/api/programs/p1/wiki/merges").json()
    assert body[0]["id"] == "m0001"


def test_accepting_applies_and_reports_it(substrate):
    _seed(substrate)
    r = _client(substrate).post("/api/programs/p1/wiki/merges/m0001/accept")
    assert r.status_code == 200 and r.json()["applied"] is True


def test_accepting_an_unknown_proposal_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).post(
        "/api/programs/p1/wiki/merges/m9999/accept").status_code == 404


def test_rejecting_reports_the_pair(substrate):
    _seed(substrate)
    body = _client(substrate).post("/api/programs/p1/wiki/merges/m0001/reject").json()
    assert sorted(body["rejected"]) == ["concepts/a.md", "concepts/b.md"]


def test_activity_is_served(substrate):
    _seed(substrate)
    assert _client(substrate).get(
        "/api/programs/p1/wiki/activity").json()[0]["id"] == "r0001"


def test_the_policy_can_be_set_and_a_bad_one_is_400(substrate):
    _seed(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p1/wiki-merge-policy",
                  json={"policy": "auto"}).json()["wiki_merge"] == "auto"
    assert c.post("/api/programs/p1/wiki-merge-policy",
                  json={"policy": "nope"}).status_code == 400


def test_the_policy_on_a_missing_program_is_404(substrate):
    _seed(substrate)
    assert _client(substrate).post("/api/programs/ghost/wiki-merge-policy",
                                   json={"policy": "auto"}).status_code == 404


def test_the_lint_route_carries_the_filed_reports(substrate):
    _seed(substrate)
    assert "reports" in _client(substrate).get("/api/programs/p1/wiki/lint").json()


def test_a_literal_wiki_route_is_not_swallowed_by_the_page_catch_all(substrate):
    """/wiki/merges must not resolve as a page slug named 'merges'."""
    _seed(substrate)
    assert isinstance(_client(substrate).get("/api/programs/p1/wiki/merges").json(), list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_merge.py -q`
Expected: FAIL — 404s from routes that do not exist.

- [ ] **Step 3: Write minimal implementation**

Add a body model beside the other wiki `*In` classes:

```python
class WikiMergePolicyIn(BaseModel):
    policy: str
```

Add the GETs immediately after `/wiki/lint` and **before** `/wiki/pages/{slug:path}`:

```python
    @api.get("/programs/{program_id}/wiki/activity")
    def wiki_activity(program_id: str) -> list[dict]:
        return service.wiki_activity(program_id)

    @api.get("/programs/{program_id}/wiki/merges")
    def list_wiki_merges(program_id: str) -> list[dict]:
        return service.list_wiki_merges(program_id)
```

and the POSTs beside the other wiki writes:

```python
    @api.post("/programs/{program_id}/wiki/merges/{merge_id}/accept")
    def accept_wiki_merge(program_id: str, merge_id: str) -> dict:
        try:
            return service.accept_wiki_merge(program_id, merge_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"no such proposal: {merge_id}")

    @api.post("/programs/{program_id}/wiki/merges/{merge_id}/reject")
    def reject_wiki_merge(program_id: str, merge_id: str) -> dict:
        try:
            return service.reject_wiki_merge(program_id, merge_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"no such proposal: {merge_id}")
```

and beside `wiki-model`:

```python
    @api.post("/programs/{program_id}/wiki-merge-policy")
    def set_program_wiki_merge(program_id: str, body: WikiMergePolicyIn) -> dict:
        try:
            return service.set_program_wiki_merge(program_id, body.policy)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_wiki_merge.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Run the whole backend suite**

Run: `~/venvs/coscience/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/http_api.py tests/test_http_wiki_merge.py
git commit -m "feat(wiki): HTTP endpoints for merges, activity and merge policy"
```

---

### Task 9: `api.ts` — types and client methods

**Files:**
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts` (extend; create only if absent)

**Interfaces:**
- Produces: `WikiMergeProposal`, `WikiRun`, `WikiLintReport.reports`, `WikiSummary.wiki_merge` / `.merge_proposals`, and `api.listWikiMerges`, `api.acceptWikiMerge`, `api.rejectWikiMerge`, `api.getWikiActivity`, `api.setWikiMergePolicy`. Tasks 10–13 use these exact names.

Read the existing wiki block in `api.ts` (~line 444) and follow it exactly — same `fetch` + `j<T>` idiom, same ordering.

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { api } from "./api";

describe("wiki merge client", () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({}),
    }) as never;
  });

  it("lists proposals for a program", async () => {
    await api.listWikiMerges("p1");
    expect(global.fetch).toHaveBeenCalledWith("/api/programs/p1/wiki/merges");
  });

  it("accepts and rejects by proposal id", async () => {
    await api.acceptWikiMerge("p1", "m0001");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/programs/p1/wiki/merges/m0001/accept", { method: "POST" });
    await api.rejectWikiMerge("p1", "m0001");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/programs/p1/wiki/merges/m0001/reject", { method: "POST" });
  });

  it("fetches the activity trail", async () => {
    await api.getWikiActivity("p1");
    expect(global.fetch).toHaveBeenCalledWith("/api/programs/p1/wiki/activity");
  });

  it("sends the merge policy as a body, not a query", async () => {
    await api.setWikiMergePolicy("p1", "propose");
    const [url, init] = (global.fetch as never as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(url).toBe("/api/programs/p1/wiki-merge-policy");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ policy: "propose" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: FAIL — `api.listWikiMerges is not a function`

- [ ] **Step 3: Write minimal implementation**

Types, beside `WikiSummary`:

```typescript
export interface WikiMergeProposal {
  id: string; winner: string; loser: string; why: string; run: string; at: number;
}
export interface WikiRun {
  id: string; kind: string; status: string; at: number;
  pages_created?: number; pages_updated?: number;
  merged: [string, string][];     // [loser, winner] pairs
}
```

Extend `WikiSummary` with `wiki_merge: "auto" | "propose"; merge_proposals: number;` and `WikiLintReport` with `reports: { date: string; text: string }[];`.

Methods, in the wiki block:

```typescript
  listWikiMerges: (id: string) =>
    fetch(`/api/programs/${id}/wiki/merges`).then(j<WikiMergeProposal[]>),
  acceptWikiMerge: (id: string, mid: string) =>
    fetch(`/api/programs/${id}/wiki/merges/${mid}/accept`, { method: "POST" })
      .then(j<{ applied: boolean; winner: string; loser: string; rewritten: string[] }>),
  rejectWikiMerge: (id: string, mid: string) =>
    fetch(`/api/programs/${id}/wiki/merges/${mid}/reject`, { method: "POST" })
      .then(j<{ rejected: string[] }>),
  getWikiActivity: (id: string) =>
    fetch(`/api/programs/${id}/wiki/activity`).then(j<WikiRun[]>),
  setWikiMergePolicy: (id: string, policy: string) =>
    fetch(`/api/programs/${id}/wiki-merge-policy`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy }),
    }).then(j<{ id: string; wiki_merge: string }>),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean. Existing `WikiSummary` mocks in `WikiView.test.tsx` will need the two new fields — add them.

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add frontend/src/api.ts frontend/src/api.test.ts frontend/src/views/WikiView.test.tsx
git commit -m "feat(wiki): api client for merges, activity and merge policy"
```

---

### Task 10: `WikiLintView` — activity and reports

**Files:**
- Create: `frontend/src/views/WikiLintView.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/views/WikiLintView.test.tsx`

**Interfaces:**
- Consumes: `api.getWikiActivity`, `api.getWikiLint` (Task 9).
- Produces: the route `/programs/:id/wiki/lint`. Task 11 adds proposals to this file, Task 12 adds findings.

Follow `WikiView.tsx`'s react-query idiom exactly (`useQuery` with a `queryKey` array, `EmptyState` for nothing-to-show). Copy the `matchMedia` / `ResizeObserver` stubs from `WikiView.test.tsx`'s `beforeEach` — jsdom has neither and Mantine reads both.

- [ ] **Step 1: Write the failing test**

```typescript
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WikiLintView from "./WikiLintView";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as never;
  window.ResizeObserver = window.ResizeObserver || (class {
    observe() {} unobserve() {} disconnect() {}
  } as never);
});

const runs = [
  { id: "r0002", kind: "lint", status: "ok", at: 1756200000, pages_created: 0,
    pages_updated: 4, merged: [["concepts/job-lease.md", "concepts/compute-lease.md"]] },
  { id: "r0001", kind: "ingest", status: "failed", at: 1756100000, pages_created: 0,
    pages_updated: 0, merged: [] },
];

const lint = {
  counts: { warn: 1 }, findings: [],
  reports: [{ date: "2026-08-27", text: "# What I did\n\nTidied two pages.\n" }],
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p1/wiki/lint"]}>
        <Routes>
          <Route path="/programs/:id/wiki/lint" element={<WikiLintView />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>,
  );
}

describe("WikiLintView", () => {
  beforeEach(() => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue(runs as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(lint as never);
    vi.spyOn(api, "listWikiMerges").mockResolvedValue([] as never);
  });

  it("lists what recent runs did, newest first", async () => {
    mount();
    const rows = await screen.findAllByTestId("wiki-run");
    expect(rows[0].textContent).toContain("r0002");
    expect(rows[1].textContent).toContain("r0001");
  });

  it("names both pages of a merge and links to the substrate commit", async () => {
    /* Nothing gates an automatic merge but git, so this row IS the audit trail. */
    mount();
    const row = (await screen.findAllByTestId("wiki-run"))[0];
    expect(row.textContent).toContain("compute-lease");
    expect(row.textContent).toContain("job-lease");
  });

  it("shows a failed run rather than hiding it", async () => {
    mount();
    const row = (await screen.findAllByTestId("wiki-run"))[1];
    expect(row.textContent).toMatch(/failed/i);
  });

  it("renders the agent's own filed report", async () => {
    mount();
    expect(await screen.findByText(/Tidied two pages/)).toBeTruthy();
    expect(screen.getByText("2026-08-27")).toBeTruthy();
  });

  it("shows an empty state when nothing has run yet", async () => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue([] as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    mount();
    expect(await screen.findByText(/nothing has run/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiLintView.test.tsx`
Expected: FAIL — cannot resolve `./WikiLintView`.

- [ ] **Step 3: Write minimal implementation**

Create `WikiLintView.tsx` with a `Stack` of `Card` sections. Requirements the tests pin:

- A `BackLink` to `/programs/:id/wiki`.
- **Activity**: one element per run carrying `data-testid="wiki-run"`, newest first (the API already returns them that way — do not re-sort), showing run id, kind, status and the local time. For each `merged` pair render "**&lt;loser slug&gt; → &lt;winner slug&gt;**"; show slugs, not full paths, and link the winner to `/programs/:id/wiki/<winner without .md>`.
- **Reports**: each filed report's date as a heading with its text through `<Md>`.
- **Findings**: leave a placeholder `<Card>` — Task 12 fills it.
- When activity and reports are both empty, render an `EmptyState` whose text includes "nothing has run".

Register the route in `App.tsx` **before** the `/programs/:id/wiki/*` catch-all, or `WikiView` swallows it:

```tsx
              <Route path="/programs/:id/wiki/lint" element={<WikiLintView />} />
```

`WIDE_ROUTE` at `App.tsx:172` already matches `/programs/<id>/wiki/…`, so this page gets the wide canvas with no change.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiLintView.test.tsx`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiLintView.tsx frontend/src/views/WikiLintView.test.tsx frontend/src/App.tsx
git commit -m "feat(wiki): maintenance view — what the runs actually did"
```

---

### Task 11: Proposals — accept and reject in the UI

**Files:**
- Modify: `frontend/src/views/WikiLintView.tsx`
- Test: `frontend/src/views/WikiLintView.test.tsx` (extend)

**Interfaces:**
- Consumes: `api.listWikiMerges`, `api.acceptWikiMerge`, `api.rejectWikiMerge` (Task 9).

Spec §11.3: proposals appear **only** when the program's policy is `propose`. Use `useMutation` with `onSuccess` invalidating the proposals and activity queries, following `WikiView.tsx`'s existing mutation idiom.

- [ ] **Step 1: Write the failing test**

```typescript
describe("WikiLintView proposals", () => {
  const proposals = [{
    id: "m0001", winner: "concepts/compute-lease.md", loser: "concepts/job-lease.md",
    why: "Both describe a time-bounded claim on a worker slot.",
    run: "r0002", at: 1756200000,
  }];

  beforeEach(() => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue([] as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    vi.spyOn(api, "listWikiMerges").mockResolvedValue(proposals as never);
  });

  it("shows the agent's reasoning and both pages", async () => {
    mount();
    const card = await screen.findByTestId("merge-proposal");
    expect(card.textContent).toContain("time-bounded claim");
    expect(card.textContent).toContain("compute-lease");
    expect(card.textContent).toContain("job-lease");
  });

  it("says which page survives, because that is the irreversible half", async () => {
    mount();
    const card = await screen.findByTestId("merge-proposal");
    expect(card.textContent).toMatch(/job-lease[\s\S]*compute-lease/);
  });

  it("accepts a proposal by id", async () => {
    const accept = vi.spyOn(api, "acceptWikiMerge")
      .mockResolvedValue({ applied: true, winner: "", loser: "", rewritten: [] } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /accept/i }));
    await waitFor(() => expect(accept).toHaveBeenCalledWith("p1", "m0001"));
  });

  it("rejects a proposal by id", async () => {
    const reject = vi.spyOn(api, "rejectWikiMerge")
      .mockResolvedValue({ rejected: [] } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /reject/i }));
    await waitFor(() => expect(reject).toHaveBeenCalledWith("p1", "m0001"));
  });

  it("says nothing is waiting when there are no proposals", async () => {
    vi.spyOn(api, "listWikiMerges").mockResolvedValue([] as never);
    mount();
    expect(screen.queryByTestId("merge-proposal")).toBeNull();
  });
});
```

Add `fireEvent` and `waitFor` to the existing `@testing-library/react` import.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/WikiLintView.test.tsx`
Expected: FAIL — no `merge-proposal` element.

- [ ] **Step 3: Write minimal implementation**

A **Proposals** card rendered above Activity when `proposals.length > 0`. Each proposal is an element with `data-testid="merge-proposal"` showing, in this order: the loser's slug, an arrow, the winner's slug, then the `why` text, then `Accept` and `Reject` buttons. Both slugs link to their pages. Disable both buttons while either mutation `isPending` so a double click cannot fire twice.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/views/WikiLintView.test.tsx`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiLintView.tsx frontend/src/views/WikiLintView.test.tsx
git commit -m "feat(wiki): accept or reject a merge proposal from the dashboard"
```

---

### Task 12: Findings — on the maintenance page and in the page you are reading

**Files:**
- Modify: `frontend/src/views/WikiLintView.tsx`, `frontend/src/views/WikiView.tsx`
- Test: `frontend/src/views/WikiLintView.test.tsx`, `frontend/src/views/WikiView.test.tsx` (extend both)

**Interfaces:**
- Consumes: `api.getWikiLint` — `findings: {rule, severity, path, message}[]` (shape unchanged since phase 2).

Spec §11.3: *"The maintenance page answers 'is this wiki healthy'; the strip answers 'is this page sound', and a reader curating a page should not have to leave it to find out."*

- [ ] **Step 1: Write the failing test**

In `WikiLintView.test.tsx`:

```typescript
describe("WikiLintView findings", () => {
  const findings = [
    { rule: "rel/no-source", severity: "error", path: "concepts/a.md", message: "no source" },
    { rule: "rel/no-source", severity: "error", path: "concepts/b.md", message: "no source" },
    { rule: "page/stub", severity: "warn", path: "concepts/c.md", message: "too short" },
  ];

  beforeEach(() => {
    vi.spyOn(api, "getWikiActivity").mockResolvedValue([] as never);
    vi.spyOn(api, "listWikiMerges").mockResolvedValue([] as never);
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: { error: 2, warn: 1 }, findings, reports: [] } as never);
  });

  it("groups findings by rule with a count", async () => {
    mount();
    const group = await screen.findByTestId("finding-group-rel/no-source");
    expect(group.textContent).toContain("2");
  });

  it("links each finding to its page", async () => {
    mount();
    const group = await screen.findByTestId("finding-group-rel/no-source");
    const link = group.querySelector('a[href="/programs/p1/wiki/concepts/a"]');
    expect(link).toBeTruthy();
  });

  it("puts errors before warnings", async () => {
    mount();
    const groups = await screen.findAllByTestId(/^finding-group-/);
    expect(groups[0].getAttribute("data-testid")).toContain("rel/no-source");
  });
});
```

In `WikiView.test.tsx`, inside the existing centre-pane describe:

```typescript
  it("shows the findings for the page you are reading", async () => {
    vi.spyOn(api, "getWikiLint").mockResolvedValue({
      counts: { warn: 1 }, reports: [],
      findings: [{ rule: "page/unmerged-prose", severity: "warn",
                   path: "concepts/a.md", message: "merged from b" },
                 { rule: "page/stub", severity: "warn",
                   path: "concepts/other.md", message: "too short" }],
    } as never);
    mount("/programs/p1/wiki/concepts/a");
    const strip = await screen.findByTestId("page-findings");
    expect(strip.textContent).toContain("unmerged-prose");
    expect(strip.textContent).not.toContain("stub");   // another page's problem
  });

  it("shows no strip on a clean page", async () => {
    vi.spyOn(api, "getWikiLint").mockResolvedValue(
      { counts: {}, findings: [], reports: [] } as never);
    mount("/programs/p1/wiki/concepts/a");
    await screen.findByRole("heading", { name: /Alpha/ });
    expect(screen.queryByTestId("page-findings")).toBeNull();
  });
```

Check `WikiView.test.tsx`'s existing page mock for the real title before asserting `/Alpha/`; use whatever it actually renders.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/views/WikiLintView.test.tsx src/views/WikiView.test.tsx`
Expected: FAIL — neither element exists.

- [ ] **Step 3: Write minimal implementation**

`WikiLintView`: group `findings` by `rule`, order the groups by severity (`error`, `warn`, `info`) then by descending count. Each group is an element with `data-testid={`finding-group-${rule}`}` showing the rule, its severity, the count, and a link per finding to `/programs/:id/wiki/<path without .md>`.

`WikiView`: add a `useQuery` for `api.getWikiLint`, filter to findings whose `path` matches the open page's path, and render them above the page body in an element with `data-testid="page-findings"`. Render nothing when the list is empty — an empty strip on a clean page is noise on every page you read.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/views/WikiLintView.test.tsx src/views/WikiView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiLintView.tsx frontend/src/views/WikiLintView.test.tsx frontend/src/views/WikiView.tsx frontend/src/views/WikiView.test.tsx
git commit -m "feat(wiki): surface lint findings globally and on the page you are reading"
```

---

### Task 13: The merge-policy control

**Files:**
- Modify: `frontend/src/views/WikiView.tsx`, `frontend/src/components/ProgramSettingsModal.tsx`
- Test: `frontend/src/views/WikiView.test.tsx`, `frontend/src/components/ProgramSettingsModal.test.tsx` (extend both)

**Interfaces:**
- Consumes: `api.setWikiMergePolicy`, `WikiSummary.wiki_merge` / `.merge_proposals` (Task 9).

Follow the `ModelSelect` idiom already in `WikiView`'s header (`WikiView.tsx:122-127`) — saves on change, locks during a run.

- [ ] **Step 1: Write the failing test**

In `WikiView.test.tsx`:

```typescript
  it("shows the merge policy and saves a change", async () => {
    const set = vi.spyOn(api, "setWikiMergePolicy")
      .mockResolvedValue({ id: "p1", wiki_merge: "propose" } as never);
    mount();
    const select = await screen.findByLabelText(/merges/i) as HTMLSelectElement;
    expect(select.value).toBe("auto");
    fireEvent.change(select, { target: { value: "propose" } });
    await waitFor(() => expect(set).toHaveBeenCalledWith("p1", "propose"));
  });

  it("links to the maintenance page, badged when proposals are waiting", async () => {
    vi.spyOn(api, "getWikiSummary").mockResolvedValue(
      { ...summary, wiki_merge: "propose", merge_proposals: 2 } as never);
    mount();
    const link = await screen.findByRole("link", { name: /maintenance/i });
    expect(link.getAttribute("href")).toBe("/programs/p1/wiki/lint");
    expect(link.textContent).toContain("2");
  });
```

In `ProgramSettingsModal.test.tsx`, mirroring its existing `wiki_model` test:

```typescript
  it("saves the wiki merge policy", async () => {
    const set = vi.spyOn(api, "setWikiMergePolicy")
      .mockResolvedValue({ id: "p1", wiki_merge: "propose" } as never);
    renderModal();
    fireEvent.change(await screen.findByLabelText(/merges/i),
                     { target: { value: "propose" } });
    await waitFor(() => expect(set).toHaveBeenCalledWith("p1", "propose"));
  });
```

Read `ProgramSettingsModal.test.tsx` first and match its actual mount helper and save idiom — it may batch saves behind a button rather than saving on change.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx src/components/ProgramSettingsModal.test.tsx`
Expected: FAIL — no control labelled "merges".

- [ ] **Step 3: Write minimal implementation**

A `<select aria-label="merges">` with options `auto` ("merge duplicates automatically") and `propose` ("ask me first"), beside the model picker in `WikiView`'s header and beside `wiki_model` in `ProgramSettingsModal`. In the header, add a link to `/programs/:id/wiki/lint` labelled "maintenance", carrying `merge_proposals` as a badge when non-zero. Disable the select while a run is in flight, for the same reason the model picker is disabled: the policy is read at collect.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/views/WikiView.test.tsx src/components/ProgramSettingsModal.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run the whole frontend suite and typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS, clean.

- [ ] **Step 6: Build, to prove the bundle compiles**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 7: Commit** (ask for approval first)

```bash
git add frontend/src/views/WikiView.tsx frontend/src/views/WikiView.test.tsx frontend/src/components/ProgramSettingsModal.tsx frontend/src/components/ProgramSettingsModal.test.tsx
git commit -m "feat(wiki): a per-program merge policy, set where you can see its effect"
```

---

### Task 14: Update the charter and the handoff

**Files:**
- Modify: `docs/knowledge-charter.md`, `docs/knowledge/NEXT.md`

The charter is the only handoff surface, and its §10 handback protocol requires this. Do not skip it because the code is done — a stale charter is worse than none, because the next agent trusts it.

- [ ] **Step 1: Update the charter**

- §2 status table: phase 3 → **done**, with the commit range and test counts; phase 4 → **next**.
- The header block: date, test counts, what state the branch is in.
- §8 invariants: add *"A merge never loses `# Human notes` and never keeps `verified`"* and *"`merges_refused` is consulted before any proposal is queued or applied"*.
- Note in §2 what the live cadence run revealed, **if it has been done** — and say plainly that it has not, if it has not. Do not describe an unrun run.

- [ ] **Step 2: Rewrite `NEXT.md` as a phase-4 handoff**

Same shape as the current phase-3 one: where it stands, what phase 4 has to build, what already exists that must not be rebuilt, the rules that bite, where the record lives, budget.

- [ ] **Step 3: Commit** (ask for approval first)

```bash
git add docs/knowledge-charter.md docs/knowledge/NEXT.md
git commit -m "docs(wiki): phase 3 done — charter and handoff current"
```

---

## Done when

- `~/venvs/coscience/bin/python -m pytest` passes, with the ~60 new backend tests.
- `cd frontend && npx vitest run` passes and `npx tsc --noEmit` is clean.
- A `report.json` carrying a `merges` entry causes the merge to be **applied** on a program set to `auto`, and **queued** on one set to `propose` — proven by `tests/test_wiki_merge_beat.py`.
- Accepting a proposal in the dashboard merges the pages, rewrites everything that linked to the loser, and produces one substrate commit naming both.
- The merged page carries `merged_from`, raises `page/unmerged-prose`, and the lint document tells the agent how to clear it.
- `/programs/<pid>/wiki/lint` shows what recent runs did, the agent's filed reports, findings by rule, and any waiting proposals.
- A rejected pair is never re-proposed.
- Phases 1 and 2 are unchanged: no test from either was edited to accommodate this phase, except mock shapes gaining new fields.

## Not in this phase

**The live cadence run** — proving `ingests_since_lint` fires on a real substrate. It spends the human's Claude quota and needs their explicit go-ahead, so it is a manual step after this plan lands, not a task inside it. Point `COSCIENCE_REPO` at a **scratch copy**, never the live substrate.

Phase 4 (`wiki_graph`, `WikiGraphView`, `d3-force`, provenance backlinks on `SprintDetail` / `ArtifactDetail`). Phase 5 (wiki chat, research runs, `QUESTIONS.md` flow, MCP tools). Path-scoped `substrate.commit()` — still deferred by decision, though it is worth noting it matters more now: reverting a merge commit takes whatever else got swept into it.

## Open questions for the human

1. **`RUNS_KEPT = 50`.** Fifty runs is roughly a month of activity at the current cadence. If you want the audit trail to go back further, say so before Task 5 — it is one constant, but it is also the whole memory of what the wiki did to itself.
2. **The default policy is `auto`** (spec §9.1, charter decision log). Every existing program picks it up the moment `Program.wiki_merge` lands, since the field defaults rather than migrating. That is the intent, recorded here so it is not a surprise.
