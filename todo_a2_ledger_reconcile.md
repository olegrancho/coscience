# A2 — Wiki ledger reconciliation

Record of todo.md items A2 and A3, both completed 2026-09-03. Kept because the
Done section is trimmed at 10 and this is where the reasoning survives; the
shipped behaviour is `wiki.reconcile()` and `coscience wiki --reconcile`.

Evidence gathered against `/home/oleg/sync/bmt-share/coscience`.

## Why the ledger diverged

`wiki.py` records ingest credit at **run** granularity but the agent works at
**object** granularity. `_collect` writes `state["ingested"]` only when the run
exits 0 and leaves a parseable `report.json`. A run killed partway through has
already committed its pages to the bundle, but gets no credit for any of them.

`_count_failure` (`src/coscience/wiki.py:179`) then counts that as a content
failure and quarantines the whole batch after three strikes. Seven consecutive
rate-limit deaths on p3 therefore produced two quarantined batches and a third
in progress — all of it work that had actually landed.

## The provenance the fix rests on

Every `sources/` page carries, in frontmatter:

```yaml
origin: "result:p3-c8-parameter-harvest-result"
origin_hash: "sha256:f820fefc69e80aa0e4d8a269aa03b1dc58ba9ed68ed6b6be1b86b6684b051201"
resource: /results/p3-c8-parameter-harvest-result.md
```

`origin_hash` equal to `wiki_store.object_hash(obj)` for the live object proves
the page was written against the current content. This is the same pairing the
lint already uses for `src/hash-drift` (page hash ≠ object hash) and
`src/missing` (object with no page), so reconciliation adds no new trust
assumption — it reads a check the schema already mandates.

## Reconciliation preview

Measured across all three wiki-enabled programs:

| program | objects | ledger says ingested | quarantined | source pages hash-matching | recoverable | no source page |
|---|---|---|---|---|---|---|
| p2 | 15 | 15 | 0 | 15 | 0 | 0 |
| p3 | 28 | 0 | 8 | 12 | **12** | 16 |
| p5 | 15 | 8 | 4 | 12 | **4** | 3 |

**Zero hash drift anywhere.** p2 reconciles exactly — it is the control case,
and it agreeing is the main evidence the check is correct.

The "no source page" column is genuinely un-ingested work that still needs real
runs; it is not recoverable by bookkeeping. For p3 those 16 are mostly `@v2`/`@v3`
figure artifacts from the takeoff and supply-demand sprints.

## p3's quarantined objects — all 8 have pages on disk

```
artifact:program-review-and-research-plan@v1
result:p3-c0-raf-emergence-phase-map-result
artifact:calibrated-hydrolysis-kinetics-rates-and-length-distribution-numbers@v1
result:p3-c1-length-ceiling-escape-result
artifact:literature-coverage-sweep@v1
artifact:calibrated-kinetics-model-code@v1
artifact:prebiotic-parameter-table-with-provenance@v1
artifact:previous-work-inventory-and-reproduction-status@v3
```

p5's four are `artifact:honest-cv-report@v1`,
`result:p5-c0-lf-fusion-oracle-result`,
`result:p5-c1-results-audit-family-profile-result`,
`artifact:family-profile-audit-report@v1`.

## Outcome

Applied 2026-09-03, substrate commit `bf778fe7`. Credited 12 objects in p3 and 4
in p5, emptied both quarantines, zero drift found. p2 was unchanged at 15 — the
control case agreed, which is the main evidence the matcher is right. A second
pass credits nothing, and the commit touched only the two `state.json` files, so
no bundle page moved.

## Shipped behaviour

For each object in `wiki_store.program_objects(substrate, program_id)`:

1. Find a `sources/` page whose `origin` equals the object id.
2. No page → leave alone; it is genuinely pending.
3. Page with `origin_hash` == live hash → credit into `state["ingested"]`
   (`hash`, `at`, `run: "reconcile"`) and remove from `state["quarantined"]`.
4. Page with a differing hash → report as drift, change nothing. The content
   moved under the page and it needs a real re-ingest.

Default `--dry-run`, printing the table above. Writes go through
`wiki_store.state_guard` so it takes the same flock as a beat, and commit to the
substrate with a message naming the program and the counts.

## Verification after running

- p3 `ingested` becomes 12, `quarantined` empty.
- p5 `ingested` becomes 12, `quarantined` empty.
- p2 unchanged at 15 — a diff there means the matcher is wrong.
- `wiki_lint.run_lint` finding counts unchanged for all three; reconciliation
  touches only `.wiki/state.json`, never the bundle.
