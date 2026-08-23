# Test me now — program wiki, browse UI

The wiki's browse UI (phase 2) is code-complete and waiting on your eyes. This
file is the two-minute version; the full procedure is
**[`docs/knowledge/phase-2-ui-test-plan.md`](docs/knowledge/phase-2-ui-test-plan.md)**.

**Branch:** `feat/program-wiki`. Not committed, not merged, not deployed.
**Verified:** backend 1107 tests, frontend 147, `tsc` clean, `vite build` clean.
**Never seen in a browser.** That is the whole point of this pass.

---

## Fastest path to a live wiki (Avatar)

Don't run a fresh ingest to get data — it costs ~6 minutes and real Claude quota.
Copy the bundle phase 1's live run already produced (18 pages, lint-clean, ~6 MB):

```bash
scp -r -P 2212 stroganov@rbscomp.net:coscience-wiki-scratch ~/coscience-wiki-test
cd ~/sync/local-share/ai-coscience
( cd frontend && npm run build )            # ALWAYS, even for python-only changes
COSCIENCE_REPO=~/coscience-wiki-test ~/venvs/coscience/bin/coscience-http
```

Open <http://127.0.0.1:8000/programs/authtest/wiki> and **hard-reload
(Ctrl-Shift-R)** — otherwise you are testing the previous bundle.

`deploy.sh` does not work on Avatar (its `pip` line fails on the uv venv), hence
starting the server by hand. `coscience-http` runs no agents, so nothing spends
quota while you click.

---

## Five checks that prove it works

| # | Do | Expect |
|---|---|---|
| 1 | Open `/programs/authtest` | A "wiki" card with `open wiki →` and a pending badge |
| 2 | Click it | Three panes: tree, page, sidebar |
| 3 | Click a concept in the tree | Headings, prose, `cited from` chips |
| 4 | Click a `cited from` chip | The real result or artifact page |
| 5 | Click a link inside the page body | Another wiki page, **no full reload** |

Watch #5 closely: a real navigation, or a `.md` in the URL bar, means client-side
link interception is broken.

Then curate: **Mark verified** → dot turns teal. Change **status**. Write **human
notes** → reload → it persisted.

---

## Don't click these on a real substrate

**Ingest now** and **Lint now** launch agent runs and spend quota. Safe on the
test copy above; think twice anywhere else.

---

## Before you start, know this

The sandbox disk is **100% full** (6.7 TB of 7.0 TB, still filling ~11 MB/s from
something outside my visibility — `/root` unreadable, no sudo). Both agent loops
are **stopped**; the backend is still serving. Nothing there can be written until
your admin frees space, so the `scp` above is a read and will work, but running the
platform on the sandbox will not.

Because of that, everything was tested in WSL rather than on the sandbox. See the
test plan's environment note if you need to reproduce that setup.

---

## What is deliberately missing

Graph view (phase 4), agent lint runs and the lint report UI (phase 3), wiki chat
and question-answering (phase 5), and the `wiki_model` / `wiki_enabled` controls in
`ProgramSettingsModal`. Wanting them is expected; they are not defects.

## What I most want to hear

Not "does it work" — the suite covers that. Whether it is **pleasant to read**,
whether the trust dots say anything at a glance, whether the outline earns its
column, and whether it feels like part of the dashboard or a different app wearing
its colours. Phrase anything visual as "what I expected / what I saw".

Full detail, including the three states you have to fabricate by hand (stale page,
quarantine banner, empty bundle):
**[`docs/knowledge/phase-2-ui-test-plan.md`](docs/knowledge/phase-2-ui-test-plan.md)**
