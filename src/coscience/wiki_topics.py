"""The topic layout for a program wiki (todo L8). Pure text — no IO.

The default layout grows one page per concept a result mentions, and its ingest
appends: "over-extract", "keep what is there and add to it", "never silently
overwrite the older claim". Probing two real wikis built that way found answers
spread over three to six pages named after sprint conclusions, page openings and
index lines still stating what a later section overturns, and setup knowledge (a
model's architecture, a benchmark's make-up) absent because no sprint was about it.

This layout keeps the same directories, page types, frontmatter, relations and
sources — so the store, lint, graph and dashboard read it unchanged — and changes
what a page is and how an ingest treats it:

- a knowledge page is a TOPIC: an area a reader brings questions to, named in the
  reader's words, holding everything the program knows about it;
- it opens with the CURRENT UNDERSTANDING, which an ingest rewrites whenever a
  result changes it, and keeps what was believed before under HISTORY;
- background pages hold how things are built and set up, from the program's own
  documentation as well as its results.

It is the current layout (`wiki_layouts.CURRENT`): a new wiki starts in it, and a
wiki built in an older layout moves over by the migration run in `wiki_migrate`,
driven by the MIGRATE_* documents below. Which layout a bundle is in is written on
the first line of its CLAUDE.md, so it travels with the substrate."""
from __future__ import annotations

SCHEMA = """<!-- coscience-wiki-layout: topics v1 -->
# This wiki

You are working inside a program's knowledge wiki: interlinked markdown pages that
hold what the program knows, organised by topic. Read this file before writing
anything.

## Layout

- `index.md` — the map. Reserved; keep its frontmatter. Organised by topic, and
  every entry carries a one-line answer: what is known now, not what the page is.
- `log.md` — append-only chronology, newest first. Reserved.
- `QUESTIONS.md` — open and resolved questions. Reserved.
- `concepts/` — TOPIC pages: one per area of knowledge a reader brings questions
  to ("water in pose scoring", "model architecture", "data scaling"). A program
  needs tens of these, not hundreds.
- `entities/` — BACKGROUND pages: the named things the work is done with and on
  (a model, a scoring program, a benchmark, a dataset, a pipeline) and how each is
  built and set up.
- `syntheses/` — views across topics: where the program stands, open leads, what
  was tried and dropped, and a glossary of the program's terms.
- `sources/` — grounding pages: one per ingested result or artifact version. They
  point at the raw object and summarise it; they never hold the knowledge itself.

## Every page

```yaml
---
type: Concept                    # Concept (topic) | Entity (background) | Synthesis | Source
title: Water in pose scoring
description: One sentence stating what is known now, with its key number.
tags: [water, scoring]
status: draft                    # draft | stable | deprecated
generated: { by: coscience-wiki/<model>, at: <ISO-8601 UTC> }
sources:
  - { id: c53, resource: /sources/result-<id>.md, title: "...", last_modified: 2026-08-14 }
relations:
  - { type: refines, target: /concepts/other-topic.md, confidence: high, source: c53 }
aliases: [crystal waters, water bridges, hydration]
---
```

A topic page's sections, in this order:

```
# Current understanding   <- what is known NOW: a short answer, with numbers
# Evidence                 <- organised by sub-question, not by sprint
# History                  <- what was believed before, and what replaced it
# Contradictions           <- only what is still unresolved
# Open questions
# References               <- footnote definitions ([^c53]: ...) go here
# Human notes              <- always last, always the human's
```

A background page opens the same way — `# Current understanding`: what the thing
is and its current state, in a few sentences — then `# How it is built`, `# How the
program uses it`, `# Known limits`, `# History`, `# Contradictions`, and the same
last three. Every page opens with `# Current understanding`, so a reader always
knows where the answer is.

`# Human notes` is the human's section. Never write under it. Never invent a
`verified:` entry — trust is recorded by the platform when a human marks a page.

## Relations — the vocabulary is frozen

`is_a`, `part_of`, `requires`, `enables`, `implements`, `exemplifies`,
`measures`, `causally_precedes`, `contradicts`, `refines`, `replaces`,
`extends`. Every relation carries `confidence` (`low` | `med` | `high`) and a
`source` id from the page's `sources`, and its target is also linked from the body.

## Rules that bite

1. **Current means current.** `# Current understanding`, `description` and the
   page's index line must be true now. When a result changes what is known,
   rewrite them, and move the old claim to `# History` with what replaced it and
   when. A superseded conclusion must never still read as current anywhere on the
   page.
2. **Consolidate.** Add to an existing topic before creating a page. A new page is
   for a new area a reader would ask about, not for each finding.
3. **Name for the reader.** Title a topic by its subject in plain words ("coefficient
   refitting"), never by a conclusion ("coefficient near-optimality"). `aliases`
   carry the everyday words a reader would type.
4. **One value per quantity.** When two measurements of the same thing differ, give
   both in one place with the scope of each (which data, which version), and say
   which one to use.
5. **One meaning per word.** When a word has two senses in this program, qualify it
   every time it is used, and list both senses in the glossary.
6. **Attribute per claim** with footnotes (`[^c53]`) tied to `sources` ids, and
   link a claim's source page, not the raw result, so a reader can follow it.
7. **Never delete a page, never compute a hash, never write outside this
   directory.** When two pages are the same topic, propose a merge in the run's
   `report.json` under `"merges"`; the platform performs it. Each entry:

   {"winner": "concepts/water-in-pose-scoring.md", "loser": "concepts/hydration.md",
    "why": "one paragraph: why these are the same topic"}

   Both are bundle-relative page paths, not slugs. Never propose a `sources/` page:
   a source page stands for one real object and is bound to it.
"""

PROTOCOL = """## Protocol

1. **Read.** Read each object end to end. Note in `{scratchpad}` what it
   establishes: each finding, number, method and negative result.
2. **Place.** For each finding, find the topic it belongs to — search titles,
   aliases and `index.md`. Only when no topic covers the area does it get a new
   one. Setup knowledge (how a model, tool or benchmark is built) goes to its
   background page.
3. **Judge against what is there.** For each placed finding, read the topic's
   `# Current understanding` and decide: does it confirm it, refine it, or
   overturn it? Write that judgement in `{scratchpad}` before editing.
4. **Write.** Write the source page. Then, per topic touched: add the evidence
   under the right sub-question; if the current understanding changed, rewrite
   it and the page's `description`, and move what it replaces to `# History`
   ("until <date>, <old claim> [^old]; <new result> showed <new claim> [^new]").
   Update the page's line in `index.md` so it states what is known now.
"""

RULES = """## Rules that bite

- **Current means current.** After your edit, nothing on a touched page — its
  description, its opening, its index line — may still state a conclusion this
  run's material overturned. Move it to `# History`; do not leave it standing
  above a correction.
- **Consolidate.** Extend an existing topic before creating a page. Over-splitting
  is how a wiki like this stops answering questions: the answer ends up spread
  across pages nobody reads together.
- **A source title is never a topic.** "Sprint p3-c14 result" is a source page.
- **Keep the evidence, compress nothing that is still true.** Rewriting the
  current understanding is not licence to drop evidence: evidence stays, filed
  under its sub-question.
- **Attribute per claim** with footnotes tied to the page's `sources` ids. Put the
  definitions in `# References`, immediately before `# Human notes`.
- **`# Human notes` is protected.** Reproduce it byte for byte; end a page that
  has none with an empty one.
"""

VOICE = """## How a page must read

The reader was not in the sprint and brings a question. Answer it first.

- **Open with the answer.** `# Current understanding` is three to ten sentences
  or bullets a newcomer can use on their own: what is known, with the numbers that
  matter and what they mean, and how sure it is.
- **Expand every term on first use** and say what each number means, not just what
  it is.
- **Organise evidence by question, not by sprint.** A reader looking for "does
  water matter" should not have to read the program's history in order.
- **Write sentences, not telegraphy**, and lead each section with its point.
"""

MIGRATE_MAP = """# Wiki migration — the topic map

You are reorganising the knowledge wiki of the research program **{title}**
(`{program_id}`) from one-page-per-concept into topics. Read-only for now: your
only output is the map.

The current wiki is at `{old}`. Read `index.md` and the frontmatter
(title, description, aliases, tags) of every page under `concepts/`, `entities/`
and `syntheses/`; open a page body only when its frontmatter leaves its subject
unclear. Also read the program's goals below and skim the top-level documentation
of its working directory, `{workdir}` (README and other top-level `.md` files;
no data), for setup knowledge the wiki may lack.

Program goals:

{goals}

The new layout is described in this schema — read it carefully:

{schema}

Write `{out}` as JSON:

{{"topics": [{{"slug": "concepts/water-in-pose-scoring.md",
              "title": "Water in pose scoring",
              "aliases": ["crystal waters", "water bridges"],
              "scope": "one sentence: which questions this page answers",
              "from": ["concepts/water-mediated-contact-falsification.md", "..."]}}],
  "background": [{{"slug": "entities/<name>.md", "title": "...", "scope": "...",
                  "from": ["entities/...md"], "docs": ["<workdir file to read>"]}}],
  "syntheses": [{{"slug": "syntheses/program-status.md", "title": "...", "scope": "...",
                 "from": ["..."]}}],
  "terms": [{{"term": "family", "senses": ["protein family (Pfam)", "failure family (H16)"]}}]}}

Rules for the map:
- Every existing page under concepts/, entities/ and syntheses/ appears in the
  `from` list of at least one new page, so nothing is lost.
- Topics are areas a reader brings questions to, named in plain words. Aim for
  roughly {target} topics; merge small neighbours rather than keep them apart.
- Background pages cover how the program's main tools, models, benchmarks and
  datasets are built — including what the documentation says and no result does.
- Always include `syntheses/program-status.md` (where the program stands against
  its goals, what is open, what was tried and dropped) and `syntheses/glossary.md`.
Reply with one line: how many topics, background pages and syntheses you mapped.
"""

MIGRATE_WRITE = """# Wiki migration — write topic pages

You are writing pages of the reorganised knowledge wiki of the research program
**{title}** (`{program_id}`). The old wiki is at `{old}` (read-only). The new wiki
is at `{new}`; its `CLAUDE.md` is the binding schema — read it first. `sources/`
there is already populated with the old source pages, unchanged.

Write exactly these pages, each from the old pages listed in its `from`:

{batch}

For each page:
1. Read every old page in its `from` list completely (and, for background pages,
   the listed documentation files). Where old pages cite a source page for a
   number you are unsure of, open the source page.
2. Write the page in the new schema: `# Current understanding` first, true as of
   the latest evidence; evidence organised by sub-question; superseded claims in
   `# History` with what replaced them; only unresolved disagreements in
   `# Contradictions`. Where old pages give two values for one quantity, state
   both once with their scope.
3. Keep every finding, number and footnote that is still true; drop nothing
   because it is inconvenient. Footnotes cite `sources` ids whose `resource` is the
   `/sources/...` page, not `/results/...`.
4. Carry over any old `# Human notes` content verbatim into the new page's
   `# Human notes`, prefixed by the old page's path on its own line.
5. Links: to other new pages use the slugs in the map at `{map}`; never link an
   old page that no longer exists.

Write only the pages in your batch, only under `{new}`. Reply with one line per
page: its path and one sentence on what its current understanding says.
"""

MIGRATE_FINISH = """# Wiki migration — syntheses, index, links

The reorganised wiki of **{title}** (`{program_id}`) is at `{new}`: its topic and
background pages are written. Read its `CLAUDE.md` (binding), then every page under
`concepts/` and `entities/`. The old wiki at `{old}` is read-only.

## 1. The synthesis pages

Write these, as listed in the map `{map}` under `syntheses`. They summarise the new
topic pages and link to them; the old pages in each `from` list are only for
anything the topics lack.

- `syntheses/program-status.md` opens with where the program stands against its
  goals, then the open leads grouped by cost, then a table of what was tried and
  dropped with the topic page for each.
- `syntheses/glossary.md` lists the program's terms; every term under `terms` in
  the map has more than one sense and lists each.

## 2. Every page opens with `# Current understanding`

Check each page under `concepts/` and `entities/`; fix only headings where needed.

## 3. The index

Write `{new}/index.md`: keep the frontmatter of `{old}/index.md`, then a short
"Start here" linking `syntheses/program-status.md` and `syntheses/glossary.md`,
then sections by area with one line per page — `- [Title](/concepts/x.md) — what
is known now, in one line.` — and the source pages last, grouped by kind.

## 4. Links

Pages under `sources/` were copied unchanged and still link old page names.
Repoint every such link to the new page whose `from` list contains the old one,
leaving the link text as it is. Then list every markdown link in `{new}` whose
target does not exist and fix each. Write only under `{new}`.
"""
