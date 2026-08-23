# Phase 2 browse UI — manual test plan

The automated suite (147 frontend, 1107 backend) proves the wiring. It cannot
tell you whether the page is *readable*, whether links land where a reader
expects, or whether an empty state looks broken. That is what this document is
for.

Budget: ~20 minutes for the full pass, ~5 for the smoke path.

---

## 1. Get a wiki in front of you

You need a program whose bundle has real pages. **Do not run a fresh ingest just
to get data** — it spends Claude quota and takes ~6 minutes. Copy the bundle that
already exists.

The scratch substrate from phase 1's live run lives on the sandbox at
`~/coscience-wiki-scratch` (~6 MB): program `authtest`, 18 pages across
`sources/`, `concepts/` and `entities/`, lint-clean.

**On Avatar:**

```bash
scp -r -P 2212 stroganov@rbscomp.net:coscience-wiki-scratch ~/coscience-wiki-test
cd ~/sync/local-share/ai-coscience
( cd frontend && npm run build )                 # ALWAYS — see CLAUDE.md rule 1
COSCIENCE_REPO=~/coscience-wiki-test \
  ~/venvs/coscience/bin/coscience-http
```

Then open `http://127.0.0.1:8000/programs/authtest/wiki`.

`deploy.sh` does **not** work on Avatar (its `pip` line fails on the uv venv), so
start the server by hand as above. `coscience-http` runs no agents, so nothing
will spend quota while you click.

**On the live box instead:** deploy normally, then reach it through a tunnel —
`ssh -L 8000:127.0.0.1:8000 aish-sandbox` — and use a real program. Note the live
substrate's wiki bundles do not exist yet, so you would have to run an ingest
first. Prefer the scratch copy.

**Always hard-reload (Ctrl-Shift-R)** after a build, or you are testing the old
bundle.

---

## 2. Smoke path (5 minutes)

If these five work, nothing is fundamentally broken.

| # | Do | Expect |
|---|---|---|
| 1 | Open `/programs/authtest` | A "wiki" card near the bottom, with `open wiki →` and a badge if objects are pending |
| 2 | Click `open wiki →` | Three panes: tree left, `index.md` centre, empty right |
| 3 | Click any concept in the tree | Page renders with headings, prose, and "cited from" chips |
| 4 | Click a `cited from` chip | Lands on the actual result or artifact page |
| 5 | Click a link inside the page body | Another wiki page, **without a full page reload** |

Step 5 is the one worth watching closely: if the browser navigates away or the
URL bar shows a `.md` path, client-side link interception is broken.

---

## 3. Systematic pass

### Header and counts

| Check | Expect |
|---|---|
| Type badges | One per non-empty type, counts matching the tree |
| `N pending` | Objects not yet ingested. `1` on the scratch bundle |
| `lint 0E / 0W` | Scratch bundle is lint-clean. Non-zero is fine elsewhere, but errors deserve a look |
| `last: ingest ok` | The recorded outcome of the previous run |
| Ingest now / Lint now | Both clickable. **Do not click on a real substrate** — they spend quota |

### Left pane — tree and search

| Check | Expect |
|---|---|
| Grouping | Concepts / Entities / Syntheses / Sources / Questions, only groups that exist |
| Trust dots | Hollow = unverified, filled grey = machine-confirmed, teal = human-reviewed. Hover shows the tier |
| Type a query | Results replace the tree, each with a highlighted excerpt |
| Clear the query | The grouped tree comes back |
| Search a word in a title vs only in a body | Title hit ranks first |

### Centre pane — the page

| Check | Expect |
|---|---|
| Markdown | Headings, lists, tables, code blocks all render. A long code block scrolls **inside** the page, not the whole window sideways |
| `cited from` chips | Result → `/results/:id`, artifact → `/programs/:id/artifacts/:aid`, sprint → `/sprints/:id` |
| An unroutable source | Shown as plain text, not a dead link. Hover shows the raw resource |
| Footnote markers (`[^c13]`) | Render as footnotes, not literal text |
| External link | Opens in a new tab |
| `# Human notes` section | Visible in the body if the page has one |

### Right pane — outline, relations, backlinks, trust

| Check | Expect |
|---|---|
| Outline | One entry per heading, indented by level. Clicking scrolls to that section |
| Relations | Typed (`part_of`, `requires`, …) with the target's title as a link |
| A broken relation | Reads `<target> (missing)` as text, not a link |
| Backlinks | Pages linking here, with relation types in parentheses where typed |
| A page with no relations or backlinks | Empty sections, not an error or a stray heading with nothing under it |

### Curation — the point of the milestone

| # | Do | Expect |
|---|---|---|
| C1 | Click **Mark verified** | Trust flips to `human-reviewed`, dot turns teal in both the panel and the tree |
| C2 | Reload the page | The verification persisted |
| C3 | Change **status** to `stable` | Badge next to the title updates |
| C4 | Type in **human notes**, click **Save notes** | Text persists after a reload, and appears in the body's `# Human notes` section |
| C5 | Check git in the substrate | `git -C ~/coscience-wiki-test log --oneline -5` shows one commit per action |

C5 matters: if a curation action does not produce a commit, the change is not
durable. Note the commits sweep in anything else dirty in the substrate — a known
platform-wide issue (`substrate.py:549` does `git add -A`), not a wiki bug.

---

## 4. States you have to fabricate

Three things cannot be seen on a healthy bundle. Each needs one command, run
against the **test copy** only.

**A stale page.** Edit any page's frontmatter, then reload:

```bash
sed -i 's/^status: .*/status: stable\nstale_after: "2020-01-01"/' \
  ~/coscience-wiki-test/programs/authtest/wiki/concepts/compute-lease.md
```
Expect an orange ⚠ beside it in the tree, tooltip "stale".

**A quarantine banner.**

```bash
~/venvs/coscience/bin/python - <<'EOF'
from coscience.substrate import Substrate
from coscience import wiki_store
s = Substrate("/home/oleg/coscience-wiki-test")     # your path
with wiki_store.state_guard(s, "authtest") as st:
    st["quarantined"] = ["result:fake-object"]
EOF
```
Expect an orange banner with **Retry quarantined**. Click it: banner disappears,
pending count rises by one.

**An empty bundle.** Visit the wiki of a program that never ran an ingest.
Expect empty panes and zero counts — *not* a spinner that never resolves or a
crash.

---

## 5. Layout and edge cases

| Check | Expect |
|---|---|
| Narrow the window below ~1100px | Three panes stack into one column, centre stays readable |
| A very long page | Only the page scrolls; the tree and right pane stay usable |
| Hand-type a bad URL: `/programs/authtest/wiki/concepts/nope` | Clean empty state, not a crash |
| Hand-type `/api/programs/authtest/wiki/pages/../../../etc/passwd` | `404`, no file contents. Covered by tests, worth confirming live |
| A page whose title has punctuation or a slash-like name | Renders and is reachable from the tree |

---

## 6. What I would most like to know

The suite cannot answer these. They are judgement calls, and they are the reason
to do this by hand:

1. **Is the page pleasant to read?** Line length, heading rhythm, whether the
   `cited from` chips crowd the top of a page with many sources.
2. **Do the trust dots communicate anything at a glance,** or are they noise?
   Unverified is deliberately hollow rather than grey — does that read as "not
   checked" or as "broken"?
3. **Is the outline worth its column** on a short page, or should it collapse?
4. **Does the wiki feel like part of the dashboard,** or like a different app
   wearing its colours?
5. **What is missing** that you reached for and could not find.

Report anything visual as "what I expected / what I saw", and I will treat a
disagreement with the spec as a bug in whichever of the two is wrong.

---

## Not in this phase

No graph view (phase 4), no agent lint runs on a cadence or a lint report UI
(phase 3), no wiki chat or search-by-question (phase 5). The `wiki_model` /
`wiki_enabled` controls are not in `ProgramSettingsModal` yet either. If you find
yourself wanting those, that is expected rather than a defect.
