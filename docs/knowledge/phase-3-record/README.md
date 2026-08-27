# Phase 3 record — merges and the maintenance page

The SDD workspace this came from is git-ignored and does not travel between
machines. These three files are the parts worth keeping.

| File | What it is |
|---|---|
| `execution-ledger.md` | The full execution ledger: the pre-flight conflict scan, every task's commits, every review verdict, and every `Ruling:` made while the human was away — each with its reasoning and its cost-if-wrong. |
| `final-review-findings.md` | The whole-branch review's nine findings, with `file:line` and a concrete failure scenario for each. All were fixed; kept because the failure scenarios explain *why* several non-obvious pieces of the merge path are shaped the way they are. |
| `final-fix-report.md` | What the fix wave changed, the test covering each finding, and the exact defect substituted to prove each test can fail. |

## The one lesson worth carrying into phase 4

**This plan produced eight tests that passed without ever exercising their target.**
Every one was caught by a subagent, none by the plan's author. The forms varied:

- a `substrate` fixture with no git repo, so a commit assertion passed vacuously
- a beat that never launched, so nine tests exercised nothing
- an assertion whose own `sorted()` did the work the implementation was meant to do
- a test named for a link that never queried an anchor
- a route-ordering test that could not detect mis-ordering
- a sort test whose fixture was already in the expected order, so it passed with no
  sort call at all
- a negative frontend assertion with no `await`, running while the component still
  rendered a spinner

The habit that caught the last few, and the one to keep: **proving a test CAN fail is
not enough — the break you substitute must be the specific mistake the test exists to
catch.** Breaking it some other way produces a green light on a test that would never
notice the real regression.

A related trap, from the same session: verification tooling has blind spots too. A
single-line `grep` for `Finding("<rule-id>"` undercounted the lint rules, because one
id sits on the line after its constructor — and that bad count was then written into
two handoff documents before an implementer disputed it.
