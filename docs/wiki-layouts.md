# Wiki layouts and migrating between them

A program's wiki is built in a **layout**: the page schema its bundle's `CLAUDE.md`
states, and the rules its ingest runs follow. Layouts are versioned, so the wiki's
shape can change without stranding the wikis already built.

## Layouts

| Layout | What a knowledge page is | Status |
|---|---|---|
| `concepts v1` | One page per concept a result mentions; ingests append new findings below the old ones | Older. Still served and ingested until migrated |
| `topics v1` | One page per topic a reader brings questions to. Each opens with `# Current understanding`, which ingests rewrite when a result overturns it, and keeps what was believed before under `# History`. Background pages hold how the program's tools, models and benchmarks are built | **Current.** New wikis start in it |

Why topics replaced concepts, with the evidence (probe scores, the ingest test):
the L-block entries in `todo.md` and their QC notes.

## Where a wiki's layout is recorded

On the first line of its bundle's `CLAUDE.md`:

```
<!-- coscience-wiki-layout: topics v1 -->
```

The record travels with the substrate, so every instance serving it agrees. A bundle
without the line predates the scheme and is `concepts v1`. Nothing ever rewrites an
existing bundle's `CLAUDE.md` except a migration.

Wiki settings (the Settings button on a program's wiki page) show the layout, and
whether a newer one is available.

## Migrating a wiki (operators)

After deploying a version that brings a new layout, each existing wiki keeps working
in its old one until you migrate it. To migrate:

- **One program, from the dashboard:** Wiki settings → *Migrate to …*.
- **Every program at once:** `coscience wiki --repo <substrate> --migrate`
- **One program, from the command line:** add `--program <id>`.

Then leave the dispatch loop running: the wiki beat runs the migration one step at a
time, like any other wiki run (same usage gate, same admission slot):

1. **setup** — a staging bundle under `programs/<id>/.wiki/migration/` gets the
   current schema, the source pages, the log and the questions.
2. **map** — one agent run reads the old wiki (and the program's workdir docs) and
   writes the topic map.
3. **write** — one agent run per batch of ~5 pages.
4. **finish** — one run writes the summary pages and the index and repoints links.
5. **swap** — the old bundle moves to `programs/<id>/.wiki/archive/<layout>-<date>/`
   and the new one takes its place. A sweep (heavy lint) follows on its own, to clean
   the source pages, which were copied unchanged.

While a migration is under way the live wiki keeps serving, untouched, and nothing
else runs on it: new results wait and are ingested into the new wiki after the swap.
Progress shows in Wiki settings and in `coscience wiki --status`.

**Cost:** tens of pages per program means roughly `pages / 5 + 3` agent runs on the
program's wiki model — about 7 for a 50-page wiki and 11 for a 135-page one on the
wikis this was calibrated on.

**If a step fails** it is retried; after `COSCIENCE_WIKI_MAX_FAILURES` failures the
migration stops with an error, the old wiki still live. *Resume* in Wiki settings, or
`--migrate` again, retries the failed step. `--migrate-cancel` (or *Cancel migration*)
discards the staging bundle; it waits for a running step to finish first.

**To undo** a finished migration, move the archived bundle back over
`programs/<id>/wiki/`; its `CLAUDE.md` still names its layout, so runs follow it again.

## Sweeps

A sweep is an agent run that reads across the whole wiki for what the mechanical
lint cannot see: an opening that a later section overturned, one quantity given two
values, a name or number that does not match its source. It runs after every 10 wiki
runs by default; `COSCIENCE_WIKI_SWEEP_EVERY` changes the interval, and `0` turns the
schedule off. `coscience wiki --sweep --program <id>` asks for one now. Set the
variable wherever the dispatch loop reads its environment and restart it.

## Adding a layout (developers)

1. Write the new schema and ingest rules. The current one's are in
   `src/coscience/wiki_topics.py`; the schema's first line is its marker.
2. In `src/coscience/wiki_layouts.py`: add the `Layout`, point `CURRENT` at it,
   map the previous one to it in `UPGRADES`, and return its schema from `schema()`.
3. Make `wiki_prompts.render_ingest` pick the new rules for the new layout.
4. Write the migration's instructions. `wiki_migrate` drives map → write → finish →
   swap with the `MIGRATE_*` documents; a change that is not a reorganisation (say,
   a new required section) may need only a finish-style step — adapt `wiki_migrate`
   and keep the staging-then-swap shape, so the live wiki is never half-migrated.
5. Test it on a copy first. The probe (`python -m coscience.wiki_probe --bundle
   <copy> --mode agent|nav --grade <model>`) answers a question set against any
   bundle, so old and new can be compared before anyone migrates a real wiki.
6. Raise the version's second digit when shipping a new layout (see CLAUDE.md,
   deploy rule 4), and say in the release note that wikis need migrating.
