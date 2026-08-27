"""Instruction documents for wiki runs. Pure — no IO, no substrate access.

The run is unattended, so everything the agent needs must be in the document:
the schema, the frozen vocabulary, the rules that bite, the absolute paths of the
objects to read, and their precomputed hashes. The bundle's own CLAUDE.md loads
automatically (cwd is the bundle), so this document carries the task, not the
schema — except where a rule is important enough to say twice."""
from __future__ import annotations

from pathlib import Path

from coscience.wiki_store import WikiObject

_PROTOCOL = """## Protocol — four passes, each written out before the next begins

1. **Structure map.** Read each object end to end. Write, into
   `{scratchpad}`, a map of its sections and what each establishes. Do not
   extract yet.
2. **Claim-level extraction.** For each object, list every distinct claim,
   mechanism, parameter, method, named thing and negative result, each with the
   section it came from. Over-extract: a concept mentioned once is still worth a
   page; a concept never written down is knowledge lost.
3. **Relationship triples.** Turn the extraction into `(source, relation,
   target)` triples using the frozen vocabulary only. Each triple carries a
   confidence and the source id supporting it.
4. **Cross-wiki contradiction scan.** Read the existing pages your new material
   touches. Where the new material contradicts them, record a `contradicts`
   relation and write a `# Contradictions` section on the page that asserts it.
   Never silently overwrite the older claim.

Only after all four passes do you write pages.
"""

_RULES = """## Rules that bite

- **Merge first.** Before creating a page, search `concepts/`, `entities/` and
  `syntheses/` for an existing page covering the same idea — check titles AND
  `aliases`. Extend the existing page and add an alias. Near-duplicates are the
  main way a wiki like this rots.
- **A source title is never a concept.** "Sprint p3-c14 result" is a source
  page. The concepts are what it established.
- **Preserve layered insight.** When you extend a page, keep what is there and
  add to it. Do not compress an existing page to make room.
- **Attribute per claim** with markdown footnotes (`[^id]`) tied to an id in the
  page's `sources` list. Put the footnote *definitions* (`[^id]: ...`) in their own
  `# References` section immediately **before** `# Human notes`, never at the end of
  the file. Markdown convention puts them last, and last is inside the one section
  you may not write in — where they show up in the human's notes box instead.
- **The containment invariant:** every typed relation in `relations` must also be
  linked from the body as an ordinary markdown link. The frontmatter is a typed
  overlay on the prose, never a substitute for it.
- **`# Human notes` is protected.** If a page has that section, reproduce it byte
  for byte. It is a human's correction and outranks anything you would write. If a
  page has no such section, end the page with an empty one — empty, and yours to
  leave empty. Nothing you write ever goes under that heading.
"""

_VOICE = """## How a page must read

Passes 1-3 produce compressed extraction notes. Those are your working material,
not your output. A page that reads like the notes has failed even when every fact
on it is correct — the wiki exists so someone who was not in the sprint can learn
from it later, and dense shorthand is exactly what stops that.

- **Open with a definition a newcomer can use.** The first sentence of
  `# Definition` says what the thing *is*, in plain language, without depending on
  any other page. "Hydrolysis is the rate at which the backbone breaks down — the
  loss term that replication has to outrun." Not "The loss term opposing template
  replication under the kinetic model."
- **The page must stand alone.** Assume the reader has not read the result it came
  from and does not know this program's shorthand. Anything a reader needs in order
  to understand the first paragraph belongs on the page, not behind a link.
- **Expand every term on first use**, then use it freely: "optimal growth
  temperature (OGT)", "melting temperature (Tm) — the temperature at which half
  the protein population is unfolded". An acronym that appears undefined is a
  defect, not a style choice.
- **Say what a number means, not just what it is.** "Spearman rho = 0.42" tells a
  newcomer nothing. "Spearman rho = 0.42 — moderate rank agreement, where 1.0 is a
  perfect ordering and 0 is chance" tells them whether to care.
- **Write sentences, not telegraphy.** Two readable sentences beat one clause-laden
  40-word sentence. Break a long chain of qualifications into separate sentences.
- **Lead each section with its point.** Put the finding first and the caveats
  after, so the page is useful when skimmed.

Length is not the enemy — density is. Prefer the version a competent scientist
from a neighbouring field could read once and understand.
"""

_PROHIBITIONS = """## Prohibitions

- Do not write anywhere outside `{bundle}` (and this run's directory,
  `{run_dir}`). Everything else in the repository belongs to other systems.
- Do not run `git commit`, `git add`, `git stash`, `git checkout`, `git reset` or
  anything else that changes the repository's git state. The platform commits your
  work for you when the run is collected. A run that commits its own changes hides
  them from the checks that read the working tree.
- Do not compute or invent content hashes. The `origin_hash` values are given to
  you above; copy them exactly. A hash you compute yourself cannot detect drift.
- Do not start background tasks, long-running processes or anything that would
  outlive this turn. If the batch is too large to finish, do fewer objects well
  and say so in the report.
- Do not delete or empty any page. Propose merges in `report.json`; a human
  decides.
- Do not write a `verified:` entry. Trust is recorded by the platform when a
  human marks a page verified.
"""

_HOUSEKEEPING = """## Before you finish

1. Update `index.md` so every page you created is reachable from it.
2. Prepend one line per object to `log.md` (newest first): the date, the object
   id, and what it added.
3. Append any genuinely open question to `QUESTIONS.md` under `## Open`.
4. Write `{run_dir}/report.json`:

```json
{{"pages_created": ["concepts/a.md"], "pages_updated": ["concepts/b.md"],
  "objects": ["result:r1"], "merges": [], "notes": "one or two sentences"}}
```

   `objects` is the list of object ids from this run's batch that you actually
   covered: exactly as given above, and only the ones you finished. Anything you
   leave out comes back to you in a later run, so leaving one out is the honest
   way to do fewer objects well.
   Never list an id you were not given, and never put page paths in `objects`.

   `merges` is how you report two pages that are the same idea. You never merge
   them yourself and you never delete a page — the platform performs the merge,
   either immediately or after a human approves it. Each entry is:

   {{"winner": "concepts/compute-lease.md", "loser": "concepts/job-lease.md",
     "why": "one paragraph: why these are the same idea"}}

   Both are bundle-relative page paths, not slugs. Never propose a `sources/`
   page: a source page stands for one real object and is bound to it. If you
   have nothing to propose, write `[]`.
"""


def _object_block(objects: list[tuple[WikiObject, str]]) -> str:
    lines = []
    for obj, digest in objects:
        paths = "\n".join(f"  - read: `{p}`" for p in obj.paths)
        lines.append(
            f"### `{obj.oid}` — {obj.title}\n"
            f"{paths}\n"
            f"  - source page to write: `{obj.slug}`\n"
            f"  - `resource:` value for its frontmatter: `{obj.resource}`\n"
            f"  - `origin_hash:` value for its frontmatter: `{digest}`\n"
            f"  - `origin:` value for its frontmatter: `{obj.oid}`\n")
    return "\n".join(lines)


def render_ingest(program, bundle: Path, objects: list[tuple[WikiObject, str]],
                  run_dir: Path) -> str:
    """The full ingest instruction document, written to the run directory."""
    scratchpad = run_dir / "scratchpad.md"
    return f"""# Wiki ingest run

You are the wiki maintainer for the research program **{program.title}**
(`{program.id}`). You are running unattended: no one will answer a question, so
make the best decision you can and record the uncertainty on the page.

Program goals:

{_indent(program.goals)}

The wiki bundle is `{bundle}` — your working directory. Its `CLAUDE.md` states
the page schema and the frozen relation vocabulary; both are binding. Read it
first.

## Your task

Ingest the {len(objects)} object(s) below into the wiki. For each one: write its
grounding page under `sources/` at the slug given, then create or extend the
concept, entity and synthesis pages it establishes.

A `sources/` page is a pointer plus a summary — it must carry
`graph_excluded: true` and the sections `# Summary`, `# Section map`,
`# Notable insights`, `# Concepts extracted`. It never carries the knowledge
itself; the concept pages do.

## The batch

{_object_block(objects)}
{_PROTOCOL.format(scratchpad=scratchpad)}
{_RULES}
{_VOICE}
{_HOUSEKEEPING.format(run_dir=run_dir)}
{_PROHIBITIONS.format(bundle=bundle, run_dir=run_dir)}
"""


def render_lint(program, bundle: Path, report: str, run_dir: Path) -> str:
    """The lint instruction document: the machine report plus the judgement calls
    a script cannot make."""
    return f"""# Wiki lint run

You are the wiki maintainer for **{program.title}** (`{program.id}`), running
unattended in `{bundle}`. Its `CLAUDE.md` is binding.

The mechanical fixes have already been applied. What follows is what a script
cannot decide.

## Machine lint report

```
{report}
```

## Your task, in order

1. **Fill missing sources.** A relation or claim with no attribution is the worst
   defect here. Trace it back to a `sources/` page and attribute it, or weaken
   the claim until it is honest.
2. **Resolve contradictions.** Where two pages disagree, do not pick a winner
   silently: record a `contradicts` relation both prose and frontmatter agree on,
   and write the disagreement into `# Contradictions` on both pages.
3. **Refresh drifted sources.** A `src/hash-drift` finding means the raw object
   changed after ingest. Re-read it and update its source page; leave the
   `origin_hash` exactly as the report gives it.
4. **Propose merges for near-duplicates.** Do not merge and do not delete — write
   the proposal into `report.json` under `merges` in the shape given below, and
   add an alias so the pages are at least findable as one idea.
5. **Rewrite merged pages.** A page whose frontmatter carries `merged_from` was
   merged mechanically: its sections are two pages stacked under one heading.
   Rewrite them into one voice — one definition, one set of evidence, no
   repetition — then delete the `merged_from` key. This is the only way that
   marker ever clears, and `page/unmerged-prose` reports it until you do.
6. **Fix stubs and orphans.** A stub either grows or is folded into a fuller page.
   An orphan gets linked from `index.md` or from the page it belongs under.

{_RULES}
{_VOICE}
{_HOUSEKEEPING.format(run_dir=run_dir)}
Write your own summary of what you changed to `{run_dir}/lint-report.md`; the
platform files it under `.wiki/lint/`. Never delete a page, and never edit
`# Human notes`.

{_PROHIBITIONS.format(bundle=bundle, run_dir=run_dir)}
"""


def kickoff(kind: str, run_dir: Path) -> str:
    """The short `-p` prompt. The real instructions are a file, so a long document
    never has to survive shell quoting."""
    return (f"Read {run_dir / 'instructions.md'} and carry out the wiki {kind} run "
            f"it describes, working only inside your current directory and that run "
            f"directory. Follow it exactly, including the report it asks you to "
            f"write at the end.")


def _indent(text: str, prefix: str = "> ") -> str:
    return "\n".join(prefix + line for line in (text or "").strip().splitlines())
