FIX WAVE (dispatch ONE subagent with all of these):
F1 (Important) wiki_merge.py:117 — _relink is applied only to `others` (:141), never
   to merged.body. Spec §9.1's body-links row is unconditional. Confirmed by probe: a
   winner linking [job lease](/concepts/job-lease.md) and [[job-lease]] keeps both
   after the merge, then the loser is unlinked in the same commit — dead link + dead
   wikilink. Worse: link/wikilink is an AUTOFIX rule, so the next run_lint(fix=True)
   materialises [[job-lease]] into a real link to a deleted page — the platform
   writing a permanent link/broken into the page. Fix: relink merged.body too.
F2 (Important) wiki_merge.py:69 — _merge_bodies iterates only _headings(loser.body),
   so any loser content BEFORE the first `# ` heading, or a loser with no heading at
   all, is silently discarded and the loser is deleted in the same commit. DATA LOSS
   on the one operation declared irreversible-except-git, and stub pages (the usual
   merge losers) are the most likely to be heading-less. Fix: carry the preamble.
F3 (Important) wiki.py:244 vs service.py:1890 — _names_a_source passes the agent's RAW
   path to wiki_store.read_page (which needs `concepts/a.md`), while merge_wiki_pages
   normalises via _strip_md and accepts both spellings. Probe: "sources/result-r1"
   (no .md) -> refused under auto, QUEUED under propose. So Task 5's source-page fix
   closed only the .md spelling; §9.1's "refused under both policies" is still open for
   exactly the sloppy spelling _proposals was written to tolerate. Fix: normalise once
   at the boundary, or read f"{_strip_md(path)}.md".
F4 (Important) wiki.py:296-298 — _handle_merges catches bare Exception and appends to
   merges_refused. Task 6's fix wave deliberately split this in accept_wiki_merge
   (NotFoundError/ValueError -> refuse; anything else -> requeue and re-raise) and the
   reasoning was never carried back. Trigger is routine, not exotic: substrate.commit
   runs `git add -A` with check=True, so ANY concurrent git process holding
   .git/index.lock (the PM loop, another beat, a worker, a human running git status)
   raises CalledProcessError. Result: half-merged bundle on disk, pair PERMANENTLY
   blacklisted, and _collect's own commit at wiki.py:377 then sweeps the half-merge
   into a run-named commit — so §9.1's "each merge is its own commit and that commit
   is the undo" fails exactly in the failure case. Same shape without any error if the
   process dies between the merge commit and the state.json write (deploy.sh restarts
   loops routinely). Fix: mirror accept_wiki_merge's narrow except.
F6 (Important) wiki.py:234 — _next_merge_id takes max() over CURRENTLY PENDING
   proposals only, so ids are reused once a proposal leaves the queue (contrast
   _next_run_id at :64, which derives from last_run precisely to avoid this). Two
   tabs/users: tab A accepts m0001; a later run queues a DIFFERENT pair also as m0001;
   tab B, still showing the old card, clicks Accept and applies a destructive merge on
   a pair that human never saw. `busy` guards only within one component instance.
   Fix: monotonic high-water mark.
F7 (Important) WikiLintView.test.tsx:185-189 — EIGHTH vacuous test. No await, so the
   component is still rendering <Loader/> when queryByTestId runs; passes against an
   implementation that renders every proposal unconditionally. Fix: await a resolved
   section first.
F8 (must-fix doc) spec §9 rule table lists 21 rules, code implements 22. Missing row:
   human-notes/machine-written (error, not auto-fixable, wiki_lint.py:427, origin
   b33be05, pre-phase-3). Verified set difference is EXACTLY that one, both directions.
F9 (must-fix doc) spec §8.3's state example at :435 still shows the pre-Task-15
   "merged": [[loser, winner]] array shape. §11.3 was amended twice this phase; §8.3
   was left behind.
F10 (cheap, reclassified by the reviewer from "human's call" to should-fix)
   service.py:1800 already returns the merge commit SHA on the accept path and the
   HTTP route passes it through, but WikiLintView.tsx:66-67 discards it. So on the
   PROPOSE path — the one where a human personally authorises a destructive operation
   — the SHA Task 15 exists to capture reaches the browser and is thrown away.
   Surface it in the accept confirmation. No new state shape, no spec change.

