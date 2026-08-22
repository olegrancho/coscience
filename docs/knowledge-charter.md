# Knowledge subsystem — charter

**For:** whichever agent picks this up next (Claude, ChatGPT, or a human), on this
machine or another.
**Branch:** `feat/program-wiki`
**Design:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — the
authoritative *what and why*. This file is the *where we are and how to work*.
**Last updated:** 2026-08-21 · by: Claude Opus 5 (RBS-138384 → aish-sandbox dev)
· state: phase 1 **done** on `feat/program-wiki` (HEAD `4840531`, 1037 tests
green). All 3 Important review findings fixed; the live end-to-end run has been
performed and verified, including the Obsidian vault check. Still **not merged
and not deployed** — both need the human's explicit go-ahead.
**Start here if you are picking this up:** `docs/knowledge/NEXT.md`

> **Keep this file current.** It is the only handoff surface. Before you stop —
> finished, blocked, or out of context — update §2 (status), §7 (decision log) and
> the header above. A stale charter is worse than none, because the next agent
> will trust it.

---

## 1. What we are building, in one paragraph

Every sprint result and artifact version this platform produces currently ends
its life as a file nobody reads twice. The Program Wiki turns that stream into a
compounding knowledge base: a background beat feeds each new result and artifact
to an agent, which extracts the concepts and entities inside it, merges them into
existing pages rather than duplicating, records **typed relations** between them,
and cites every claim back to the object it came from. Every few ingests the wiki
is linted — broken links, stale claims, contradictions, orphans and duplicates
get found and fixed. Humans read and curate; they do not write the prose. The end
state is that questions get answered *from the wiki*, by people and by the
platform's own agents, instead of being re-derived from the raw pile each time.

It lives at `programs/<pid>/wiki/` in the **substrate** repo as an
[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog)
v0.2 bundle — plain markdown, portable, openable in Obsidian as a vault with zero
extra code.

---

## 2. Status

Update this table as you go. One row per phase from the design's §15.

| Phase | What | Status |
|---|---|---|
| — | Research (Karpathy llm-wiki, OKF v0.2, the `_tmp_wiki` reference impl) | **done** |
| — | Design spec | **done**, approved |
| — | Implementation plan (`superpowers:writing-plans`) | **done** — `docs/superpowers/plans/2026-08-20-program-wiki-phase-1.md` |
| 1 | Store & ingest — `wiki_store`, `wiki_okf`, `wiki_prompts`, `wiki_agent`, `wiki.beat`, `agent_stream` extraction, `wiki_lint` as CLI, `coscience wiki --once` | **done** — 25 commits, HEAD `4840531`, 1037 tests green. All 3 review findings fixed (`final-fix-report.md`); live end-to-end run performed 2026-08-21 and verified, Obsidian vault included. **Not merged, not deployed.** Three defects the live run exposed are open — see below |
| 2 | Browse — endpoints, `WikiView`, curation actions, provenance chips *(**first milestone** ends here)* | **next** |
| 3 | Lint runs — agent lint mode, cadence, report UI, quarantine retry | not started |
| 4 | Graph — `wiki_graph`, `d3-force`, `WikiGraphView`, provenance backlinks | not started |
| 5 | Ask & research — wiki chat, research runs, `QUESTIONS.md`, MCP tools | not started |

**Phase 1 is done, and the definition of done was met by a real run.** The full
record (decision log, review, deferred items, fix brief, fix report) is in
`docs/knowledge/phase-1-record/`; the running order is `docs/knowledge/NEXT.md`.

### What the first live run actually did (2026-08-21)

`coscience wiki --repo <scratch> --program authtest --once`, on a scratch copy of
the dev substrate, model `claude-sonnet-5`, ~6 minutes, exit 0.

- 4 of 5 pending objects dispatched (batch size 4): 3 results + one artifact
  version. **18 pages created**, 3 updated — 4 `sources/`, 10 `concepts/`, 4
  `entities/`.
- Page quality was better than expected: real definitions, `# Evidence` bullets
  with footnote citations back to the source oid, `# Contradictions` and
  `# Open questions` filled in honestly, correct OKF frontmatter with typed
  relations and `aliases`.
- The agent volunteered an entity for an artifact **not** in its batch
  (`how-artifacts-work`), said so in `notes`, and filed it in `QUESTIONS.md` for a
  later run to ingest properly. That is the behaviour the prompt asks for.
- `report.json`'s `objects` listed exactly the 4 dispatched oids in exact format,
  so reconciliation ingested 4 and left the 5th pending. `escaped: []`,
  `failures: 0`, `quarantined: []`.
- Lint: **5 errors → 0** after `--lint --fix` (all 5 `rel/no-link`). 19 warnings
  and 4 info remain, none of them blocking.
- The bundle opens as an Obsidian vault and the links resolve, confirmed by hand.

**Calibration for the next phase:** batch size 4 was right — one run produced 18
coherent pages without thrashing. Sonnet was sufficient; there is no evidence yet
that Opus is needed for ingest. The run cost ~6 minutes of wall clock, so the
dispatch cadence does not need to allow for long ingests.

### Three defects the live run exposed — one fixed, two open

1. **The wikilink autofix never fired on what agents actually write — FIXED.**
   `wiki_lint.autofix` resolved `[[x]]` through `by_slug = {p.slug: p.path}`, a
   *bare slug* lookup. The agent wrote `[[concepts/session-based-attribution]]`, a
   *path*, so the lookup missed, `continue` fired, and the link was left alone —
   all 19 wikilinks in the run were skipped, and a `--fix` pass reported success
   while changing nothing. `wiki_lint._wikilink_index` now maps the bare slug, the
   bundle path, and the path without `.md`; callers strip a leading `/` and a
   trailing `.md` first. Verified on the run's own bundle: **19 warnings → 0**,
   4 pages rewritten. Rewrites are labelled with the target's slug, deliberately,
   rather than echoing a whole path at the reader.
2. **The agent wrote into `# Human notes`.** That section is declared protected —
   reproduce byte for byte, it outranks agent prose. The agent put its footnote
   definitions there (`[^c13]: sources[c13] — …`). Harmless on a new page with no
   human content, but every later run must now preserve the agent's own footnote
   as though a human wrote it, and the human-notes lint rule did not flag it.
   Either the prompt must name a different home for footnote definitions, or the
   rule must catch an agent writing into that section on a page it just created.
3. **`substrate.commit()` is repo-wide.** `substrate.py:549` runs `git add -A`,
   so `.coscience/wiki.lock` was swept into the wiki's own commit. Pre-existing
   platform behaviour, not introduced by the wiki, but it makes the plan's "nothing
   outside `programs/<pid>/` changed" weaker than stated: a wiki run does not
   *write* elsewhere, but its commit *records* whatever else happened to be dirty.
   On a substrate with concurrent sprint work, unrelated changes land under a
   message reading `wiki …`. Path-scoped commits are the fix and are a platform
   change, not a wiki one.

Two smaller notes, neither a defect. The mechanical fix writes body links
bundle-absolute (`/concepts/x.md`); Obsidian resolves those, but GitHub and VS Code
preview will not — the bundle is portable to Obsidian, not to every renderer.
And `.wiki/state.json` is written *after* `substrate.commit()`, so a run's state
update always lands in the *following* commit rather than its own.

Uncommitted files in the working tree (`frontend/src/styles.css`,
`ProgramDetail.tsx`, `SprintDetail.tsx`, `PageToc.tsx`, `frontend/.coscience/`,
`docs/_tmp_wiki/`) are **someone else's in-flight work carried over from `main`.
Do not commit them.** Stage explicit paths; never `git add -A`.

---

## 3. Read these, in this order

1. `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — the design. All of it.
2. `CLAUDE.md` (repo root) — the platform rules. §4 below repeats the ones that
   will bite you.
3. `docs/sprint-lifecycle.md` — the sprint state machine, so you understand what a
   "result" is and when it appears.
4. `docs/_tmp_wiki/llm-wiki-skills/` — the reference implementation this design
   ports from. Untracked, present only on Avatar. If you are on another machine
   and it is missing, the normative rules were lifted into the design's §2.2 and
   §8.8; you are not blocked, but the prompt-writing in `wiki_prompts.py` will be
   better if you can read the original `skills/llm-wiki-ingest/SKILL.md`.
5. Then the four files whose patterns you are copying, in this order:
   `src/coscience/graph.py` (frozen vocabulary, edge ids, validation-before-write),
   `src/coscience/claude_executor.py` (how a detached `claude -p` run is launched,
   polled and collected), `src/coscience/dispatcher.py` (`run_one_cycle`, the
   per-program pass the beat hooks into), `src/coscience/artifacts.py` (the
   `_lock_guard` flock pattern the wiki's `state.json` reuses).

---

## 4. Rules that will bite you

- **Two repos.** *Code* is this repo. *Substrate* is the data — programs, sprints,
  results — a **separate git repo** at `$COSCIENCE_REPO`. The wiki bundle lives in
  the **substrate**. Code deploys never touch it. Confusing the two is the single
  most damaging mistake available here.
- **Never commit or push without explicit approval.** Ask. Every time.
- **Never `git add -A`** on this branch (see §2).
- **Linux-only runtime** — `/proc`, `os.killpg`, `fcntl`. Do not add code paths
  that assume otherwise, and do not "fix" the platform checks.
- **`npm run build` on every deploy**, even for python-only changes. The
  dashboard's version banner compares the SHA baked into the JS bundle against
  `/api/version`; skipping the build shows a false drift warning.
- **`scripts/deploy.sh` is not portable.** On Avatar it aborts (the uv venv has
  `pip3`, no `pip`). Check that host's `local_setup_*.md` before running it.
- **No live LLM in the test suite.** `wiki_agent` is the one injectable
  side-effecting seam precisely so tests can substitute a double, exactly as
  `pm_claude.ClaudeCodeReasoner` sits behind the reasoner seam. If you find
  yourself needing a real Claude call to test something, the seam is in the wrong
  place.

---

## 5. How to work on this without this machine

You need almost nothing local.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,http,mcp]'
pytest                                    # from the repo root
cd frontend && npm ci && npm test         # vitest
```

**You do not need a real substrate.** `tests/conftest.py` builds tmp substrates;
follow it. Phases 1 and 3 are entirely headless and fully testable this way —
`wiki_store`, `wiki_okf`, `wiki_graph`, `wiki_lint` and `wiki_prompts` are pure
and need no fixtures beyond files in a tmp dir.

**You do not need a Claude subscription to build phases 1–4.** Only the agent run
itself calls Claude, and it is behind the `wiki_agent` seam. Build against the
double; a human on a machine with a real substrate does the live smoke test.

**If you do have a substrate**, the CLI entry point the design specifies
(`coscience wiki --repo <substrate> --program <pid> --once`) is the way to
exercise one beat by hand without waiting on the dispatch loop. Build it early in
phase 1 — it is your only ergonomic debugging surface.

---

## 6. The seams, and why they are where they are

The design's §12 has the module list. The reasoning behind it, which the list does
not convey:

- **Pure vs IO is the primary split.** `wiki_okf`, `wiki_graph`, `wiki_lint` and
  `wiki_prompts` take data and return data. `wiki_store` is the only module that
  touches the bundle. `wiki_agent` is the only module that starts a process. This
  mirrors `graph.py`'s own note — *"Pure logic — no IO … keeping writes out of the
  reasoner (the seam rule)"* — and it is why the test suite can be fast and
  offline.
- **`wiki.py` holds the state machine and nothing else.** It decides; it does not
  parse, render, or launch. If you find branching logic creeping into
  `wiki_agent`, move it back.
- **`agent_stream.py` is an extraction, not new scope.**
  `ClaudeAgent._unwrap_envelope` and `chat_agent.collect_turn` already hold two
  near-identical stream-json scans; this work would add a third. Extract a pure
  `parse_stream(raw)` and leave each caller's *status* logic alone — it genuinely
  differs between them (interrupted handling, the cost sidecar, session-id
  capture). Behaviour-preserving; the existing tests for those two paths are your
  safety net. Do this in phase 1, before there is a third copy to keep in sync.

---

## 7. Decision log

Decisions already taken, with the reason. **Do not silently reverse one** — if
you believe a decision is wrong, say so to the user and add a row here.

| Decision | Reason |
|---|---|
| OKF v0.2 as the on-disk format | Standardizes the same pattern we converged on; makes the bundle portable; and this project already intended it (`substrate.py`'s docstring, `initial_specs.md:26`, the 2026-06-23 platform design). |
| Typed `relations` as an OKF extension, not a fork | OKF declines to define a relation taxonomy and requires consumers to tolerate unknown keys. Documented in the bundle's `CLAUDE.md`. |
| Approach C: links as substrate, typed relations as overlay, lint enforcing containment | Untyped links can't answer "what contradicts X". Typed frontmatter alone drifts from the prose. The containment invariant (`rel/no-link`) is what stops the drift. |
| Markdown links, not `[[wikilinks]]` | OKF mandates them, Obsidian resolves and graphs them anyway, so it costs nothing. Lint mechanically rewrites a wikilink whether the agent wrote a bare slug or a bundle path — the path form was unhandled until the first live run produced 19 of them. Padded (`[[ slug ]]`) is still warned and not rewritten: `wiki_okf.wikilinks` strips before the literal `str.replace` can match, the same naive-parsing limitation documented for code fences. |
| Mechanical fixes write body links bundle-absolute (`/concepts/x.md`) | Verified against Obsidian on 2026-08-21: it resolves them. Keeps one form everywhere instead of computing a per-page relative prefix. Cost, accepted knowingly: GitHub and VS Code preview do not resolve them, so the bundle is portable to Obsidian rather than to every markdown renderer. |
| Sources are pointed at, never copied | `results/` and artifact versions already are the immutable git-versioned raw layer. Copying creates a second truth. |
| `.wiki/` is a **sibling** of `wiki/`, not a child | Keeps the bundle a clean portable OKF directory — copy `wiki/` anywhere with nothing to strip. |
| Source-page slugs assigned by the platform, not the agent | So the platform can find an object's page without searching. Same reason `origin_hash` is precomputed and handed to the agent — a hash the agent invents cannot detect drift. |
| Ingest beat lives in **dispatch**, not the PM loop | Dispatch already launches/polls/collects detached agents and already iterates programs per cycle. No new process ⇒ `deploy.sh` unchanged. |
| `WIKI_THRESHOLD = 70` — below PM (80) and worker (90) | Wiki maintenance is the least urgent Claude consumer and must never starve planning or sprint work. |
| Artifacts: current version only | Otherwise a figure revised five times yields five near-identical source pages. |
| Oldest-first ingest order | Makes `log.md` a true chronology and lets later ingests reference concepts earlier ones created. |
| Quarantine after 3 failures | Without it, one malformed object silently wedges a program's wiki forever, and the symptom — nothing happening — is invisible. |
| Trust is derived from OKF `verified`, not a hand-rolled `status` enum | An earlier draft conflated lifecycle and trust in one field. They are orthogonal. |
| `# Human notes` is a protected section, enforced by lint | It is the only way "read + curate" survives re-ingest. Prompt-only enforcement is not enforcement. |

---

## 8. Invariants to check before you call any phase done

1. **One run per program at a time.** Concurrent ingests are the most reliable
   way to manufacture duplicate pages.
2. **Every typed relation also appears as a markdown link in the body**
   (approach C). Lint rule `rel/no-link`.
3. **Every relation carries a `source`.** An unattributed assertion is exactly
   what the wiki exists to prevent. Lint rule `rel/no-source`.
4. **Source pages are never graph nodes** (`graph_excluded: true`), and no concept
   page is named after a source. Lint rule `src/is-concept`.
5. **`# Human notes` survives a re-ingest** of a page that has one.
6. **A failed run cannot wedge the beat** — grace-period collect, failure counter,
   quarantine, all exercised by tests.
7. **No write escapes the bundle.** The post-run `git status` check (design §13.1)
   refuses to mark the batch ingested if anything outside
   `programs/<pid>/wiki/` and `programs/<pid>/.wiki/` changed.
8. **The suite runs offline**, in seconds, with no Claude call.

---

## 9. Where the real value is, if you have room

Two items from the design's §14 deferred list are worth more than the rest, and
the schema was shaped to accept them without migration:

- **External literature intake.** The obvious next ask, and nearly free: a source
  page from a PDF is just `origin: file:wiki-raw/foo.pdf` with the same
  `resource` + `origin_hash` shape. Needs a conversion ladder (`marker` →
  `pymupdf4llm` → `pdftotext`) and a UI drop zone.
- **Attested Computation** (OKF v0.2). A page carries a definition *and* the
  sanctioned code that computes it, with an executor producing a receipt and an
  attester checking it. On this platform that makes **"re-verify this claim" a
  sprint the system can run** — reproducibility as a first-class citizen, and the
  thing that would make this a *science* wiki rather than a notes app. It is named
  here rather than left in a backlog because it constrains the provenance schema
  now: §5 and §6 of the design are shaped so it drops in later.

---

## 10. Handback protocol

When you stop, for any reason:

1. Update §2, §7 and this file's header.
2. Leave the working tree clean of anything you did not intend, and **do not
   commit the pre-existing frontend changes listed in §2**.
3. If you are mid-phase, write what is half-done and what the next concrete step
   is — file and function, not a theme.
4. If you hit a decision the design did not settle, add it to §7 with your
   reasoning, and flag it to the user rather than burying it.
5. Ask before committing. Always.
