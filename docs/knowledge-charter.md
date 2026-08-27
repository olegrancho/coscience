# Knowledge subsystem — charter

**For:** whichever agent picks this up next (Claude, ChatGPT, or a human), on this
machine or another.
**Branch:** `feat/program-wiki`
**Design:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — the
authoritative *what and why*. This file is the *where we are and how to work*.
**Last updated:** 2026-08-26 · by: Claude Opus 5 (Avatar) · state: phases 1 and 2
**both done** on `feat/program-wiki` (1129 backend / 156 frontend tests green).
The first milestone — ingest + browse — is complete: a live bundle has been
ingested, browsed by hand and curated. Still **not merged and not deployed**;
both need the human's explicit go-ahead, and the human has said *not yet*.
**Phase 3 (lint runs) is the work in progress.**
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
| 2 | Browse — endpoints, `WikiView`, curation actions, provenance chips *(**first milestone** ends here)* | **done** — 16 commits, HEAD `975cb25`. Suites green; the manual pass was performed on a live bundle and its findings fixed. **Not merged, not deployed** |
| 3 | Lint runs — agent lint mode, cadence, report UI, quarantine retry | **next** — more of it already exists than the spec's row implies; see §2's phase-3 note |
| 4 | Graph — `wiki_graph`, `d3-force`, `WikiGraphView`, provenance backlinks | not started |
| 5 | Ask & research — wiki chat, research runs, `QUESTIONS.md`, MCP tools | not started |

**Phases 1 and 2 are done, and each met its definition of done against a real
bundle** — phase 1 by a live ingest, phase 2 by a human reading and curating what
that ingest produced. Phase 1's full record (decision log, review, deferred items,
fix brief, fix report) is in `docs/knowledge/phase-1-record/`; phase 2's is the
execution record at the end of
`docs/superpowers/plans/2026-08-21-program-wiki-phase-2.md`. The running order for
whoever picks this up is `docs/knowledge/NEXT.md`.

### What phase 3 still has to build (2026-08-26)

The spec's §15 row for phase 3 reads as four things. Three of them are already on
this branch, built in phase 1 and exercised by the suite:

- **agent lint mode** — `wiki_prompts.render_lint`, and `wiki.beat` launches
  `kind="lint"` with a machine report produced by `_lint_report`.
- **the cadence** — `ingests_since_lint` counts up in `_collect` and `lint_every()`
  is the threshold; a lint run that collects `ok` resets it, a failed one stays
  owed. `_file_lint_report` files the agent's summary under `.wiki/lint/<date>.md`.
- **quarantine retry** — `Service.unquarantine_wiki`, the endpoint, and the
  "Retry quarantined" banner in `WikiView`.

What is genuinely missing is the **report UI** — `api.wikiLintReport` and
`GET /wiki/lint` exist and are wired to nothing; the header shows only
`lint NE / NW`, so a reader can see that there are findings but not what they are,
and the filed `.wiki/lint/<date>.md` summaries are unreachable from the dashboard
(`GET /wiki/log` serves the bundle's `log.md`, not those) — and **proof that the
cadence fires on a real substrate**, which no test can give because the suite never
launches an agent. Plan phase 3 against that gap, not against the spec row. Note
`wiki_lint` now carries 22 rule ids, the spec's 20 plus the two `human-notes/`
rules from `b33be05`; diff the table against the code rather than trusting a count
in prose.

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

### What the two live passes exposed, and where each landed

Phase 1's ingest and phase 2's manual browse each surfaced defects. All of them
are closed except one, which is deferred by decision.

1. **The wikilink autofix never fired on what agents actually write — FIXED
   (phase 1).** `wiki_lint.autofix` resolved `[[x]]` through `by_slug = {p.slug:
   p.path}`, a *bare slug* lookup. The agent wrote `[[concepts/session-based-
   attribution]]`, a *path*, so the lookup missed, `continue` fired, and the link
   was left alone — all 19 wikilinks in the run were skipped, and a `--fix` pass
   reported success while changing nothing. `wiki_lint._wikilink_index` now maps
   the bare slug, the bundle path, and the path without `.md`; callers strip a
   leading `/` and a trailing `.md` first. Verified on the run's own bundle:
   **19 warnings → 0**, 4 pages rewritten.
2. **The agent wrote into `# Human notes` — FIXED (`b33be05`).** It was putting
   its footnote definitions there, because markdown convention puts them at the
   end of a file and the page template ended with that heading. The template now
   shows `# References` before it, `wiki_prompts` says so explicitly, and two lint
   rules catch it from either direction: `human-notes/footnote-definition` needs
   no previous revision so it also finds pages already written that way, and
   `human-notes/machine-written` catches the general case against the previous
   body. Re-ingesting the test program under the new prompt took lint from 12
   errors to zero findings of any severity.
3. **The pages read like extraction notes, not like a wiki — FIXED (`b33be05`).**
   Not a defect in the code: the prompt carried detailed rules on structure and
   not one word on writing for a reader, while passes 1–3 explicitly produce
   compressed notes. `_VOICE` now states the standard — open with a definition a
   newcomer can use, expand every term on first use, say what a number *means*.
   It goes into the lint document as well as the ingest one, because a lint run
   rewrites pages too.
4. **Three read-path defects from the first browse — FIXED (`9b18510`,
   `975cb25`).** `index.md`'s OKF frontmatter was served raw and rendered as the
   page's largest heading; every `cited from` chip was unroutable because
   `provenance_ref` did not know the `/sources/result-<id>.md` spelling the bundle's
   own `CLAUDE.md` tells the agent to write; `human_notes` returned the footnote
   definitions sitting in the protected section, offering machine text to a human
   to save over. Plus the index pane rendering links through a bare `<Md>` with no
   components override — every link on the landing page was a raw browser
   navigation — and `isInternalLink("#user-content-fn-c1")` being true, which sent
   every footnote marker to the wiki index instead of down the page.
5. **`substrate.commit()` is repo-wide — OPEN, DEFERRED BY DECISION
   (2026-08-26).** `substrate.py:549` runs `git add -A`, so a wiki commit records
   whatever else happened to be dirty. Phase 2 makes this more visible, not worse:
   every curation click commits. It is **pre-existing platform behaviour, not a
   wiki bug**, and path-scoped commits are a platform change touching every writer
   — sprints, results, artifacts — so it does not belong inside the wiki's phases.
   Ruled: leave it, and do not let it block the merge. Revisit it as its own task
   when someone runs concurrent sprint work against a substrate whose history has
   to stay legible.

Two smaller notes, neither a defect. The mechanical fix writes body links
bundle-absolute (`/concepts/x.md`); Obsidian resolves those, but GitHub and VS Code
preview will not — the bundle is portable to Obsidian, not to every renderer.
And `.wiki/state.json` is written *after* `substrate.commit()`, so a run's state
update always lands in the *following* commit rather than its own.

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
- **Stage explicit paths, never `git add -A`.** The carried-over frontend work
  that made this a hard rule landed on `main` and was merged in (`fe0c125`), so
  the tree is no longer a minefield — but the substrate's own `commit()` still
  sweeps (§2 item 5), and the habit is what keeps a wiki commit about the wiki.
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
| Footnote definitions go under `# References`, above `# Human notes` | Markdown convention puts them at the end of the file, so a template ending in the protected heading was *asking* the agent to write there. Moving the target is cheaper and more reliable than forbidding the habit; the two lint rules exist because prompts are not enforcement. |
| The ingest prompt carries a voice standard (`_VOICE`), not only a structure spec | The first live bundle was correct and unreadable. Passes 1–3 produce compressed notes and nothing told the agent to turn them into prose; a rule about structure cannot ask for one. |
| Planning and wiki writing get independent model dials (`wiki_model`) | They are different jobs. Sonnet was sufficient for ingest on the first live run; forcing the planner's model on it would spend Opus on extraction. The picker locks during a run because the model is captured at launch. |
| The wiki route gets its own 1360px canvas, outside the app shell's 980px measure | 980px is right for a document read top to bottom and wrong for a browser: two rails plus gaps left the reading column 448px. Three panes at 260/1fr/320 put it at ~730px, about 72 characters. |
| A forced run records `forced_by`; an unattended beat records nothing | `POST /wiki/run` spends a Claude window on someone's say-so, and the run should say whose. Access was never the gap — the `api` router gates the route like every other — attribution was. Built server-side, like `verify`'s actor, so a request cannot name someone else. |
| `substrate.commit()`'s repo-wide `git add -A` stays, for now | Path-scoped commits touch every writer on the platform, not just the wiki. Deferring it is a scope call, not a judgement that it is fine; see §2 item 5. |
| **Phase 3 (2026-08-27):** the agent proposes merges, the platform applies them — one mechanism, two authorities | Letting the agent merge directly means letting it delete pages, which every prompt forbids and which needs its own trust argument. Proposing keeps one code path for both policies, keeps the agent's prohibitions unchanged, and keeps the whole thing testable offline against a fake. Cost, accepted: a merged page reads as two stacked definitions until a later lint run tidies it — in *both* policies, not just the manual one. |
| A merged page carries `merged_from` and raises `page/unmerged-prose` | The mechanical merge cannot write prose. Rather than a new "needs cleanup" mechanism, the marker feeds the loop that already runs: lint notices, the lint agent rewrites, the marker clears. OKF round-trips unknown keys, so the marker costs nothing. |
| Merging clears `verified`, and never loses `# Human notes` | A verification is a claim about specific text, and a merge changes the text — the merged page returns to the human queue as unverified. Notes are the human's own words, so both pages' notes are folded into the survivor and labelled. (Note the related pre-existing weakness: a plain re-ingest rewrites a body and the `verified` stamp survives it.) |
| Merge relations are **retargeted**, not dropped | `Service.delete_wiki_page` drops relations pointing at the deleted page, which is right for a deletion and wrong for a merge — it would discard exactly the knowledge the merge exists to preserve. Merge must not reuse that path. |
| **Nothing gates an automatic merge but git.** Ruled by the human, 2026-08-27 | Full autonomy was the explicit ask. Each merge is its own substrate commit naming both pages, and that commit is the undo. Cost, stated and accepted: a wrong merge stays wrong until somebody notices, and nobody reads a wiki looking for absences. Two things follow rather than sit beside it — the `state["runs"]` audit trail and §11.3's activity view — and the deferred repo-wide `commit()` matters more under this ruling, since reverting a merge commit takes whatever else got swept in with it. |
| `Program.wiki_merge` defaults to `auto` | Matches the stated intent to run mostly unattended, and no real substrate carries a wiki bundle yet, so there is no installed base to surprise with a destructive default. |

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
