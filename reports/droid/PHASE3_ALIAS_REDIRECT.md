# Phase 3 — Alias/Redirect Restoration

Branch: `droid/retrieval-accuracy-v06`
Pack: `selector-10k-p3.sqlite` (10,000 docs, 147,549 chunks, 2,349 redirects folded, 16,079 aliases)
Benchmark: frozen V050 R1 (hash-verified), 1,280 ANSWER cases, pageid-level gold matching
Prior state (Phase 2, commit `phase-2`): fusion strict 69.84% / lenient 81.41%

## Changes

### Builder (`traversal/corpus.py`)
1. **Redirect folding** (`_fold_redirects` post-pass): redirect pages no longer keep
   their own title as an alias of the stub document. Each redirect *source* title now
   aliases the resolved *target* document, with doc-keyed chain resolution (depth 4,
   cycle-safe) and non-redirect preference on casefold collisions
   (e.g. `Gold Rush` -> `Gold rush` article, not the stub).
2. **Anchor-alias harvesting**: `[[target|label]]` anchor texts (len 4-60, deduped)
   are recorded as aliases of the resolved target document. 6,314 anchor aliases added.
3. Manifest gains `redirects_folded` / `anchor_alias_rows`.

### Selector (`selection/selector.py`)
1. **Alias probing** (`_alias_probed_documents`): casefolded <=5-token query windows
   matched against the alias table (batched IN query, longest non-overlapping,
   <=4 docs) — finds entities named by redirect/anchor surfaces invisible to the
   capitalized ENTITY_RE ("dancer", "1980's").
2. **Disambiguation filtering** (`_is_disambiguation` / `_drop_disambiguation`):
   title suffix / "may mean:" / "may refer to:" / "{{disambig" pages dropped from anchors.
3. **Word-boundary `alias_fit`**: substring matching replaced by a word-boundary regex.
4. **Case-variant anchor dedupe**: casefolded-title duplicates (e.g. two SpongeBob
   articles) no longer consume multiple anchor slots.
5. **Bounded expansion probe**: anchor-title expansion terms run as a separate
   12-row FTS probe instead of being concatenated into the main query, so they can
   never displace the original query's 7-term FTS budget.
6. **Refitted FUSION_WEIGHTS** (coordinate search, tuning+development only,
   feature-tag `phase3-alias-fold-v2`).

## Measurements (10k pack, all 1,280 ANSWER cases)

### Step 1 — mechanism isolation, Phase 2 weights frozen
alias strict 44.4% -> 85.6% (+41.1), redirect 76.0% -> 92.0% (+16.0), but
comparison -20.9 and two_source -10.0. Root cause found: spurious single-word
alias probes ("compare" -> Comparative, "values" -> Value (personal and cultural))
flooded the concatenated expansion query and pushed real entity terms out of the
FTS top-7 term budget. Fixed via the bounded expansion probe + anchor dedupe above.

### Step 2 — final state, weights refitted on tuning+development
| category | strict before | strict after | delta |
|---|---|---|---|
| alias | 0.4444 | 0.9000 | +0.4556 |
| redirect | 0.7600 | 0.9400 | +0.1800 |
| two_source | 0.6545 | 0.6909 | +0.0364 |
| three_to_six_source | 0.4375 | 0.4625 | +0.0250 |
| comparison | 0.5455 | 0.5455 | 0.0000 |
| direct_fact | 0.9818 | 0.9818 | 0.0000 |
| follow_up | 0.9600 | 0.9600 | 0.0000 |
| pronoun | 0.9600 | 0.9600 | 0.0000 |
| date | 0.6273 | 0.6091 | -0.0182 |
| quantity | 0.6818 | 0.6545 | -0.0273 |
| quotation | 0.7900 | 0.7800 | -0.0100 |
| misspelling | 0.1800 | 0.1700 | -0.0100 |

Overall fusion: **strict 69.84% -> 73.67% (+3.83)**, lenient 81.41% -> 83.52% (+2.11).
Fit partitions (tuning+development): 73.35% -> 75.71% (+2.36).
Held-out (informational): evaluation +6.08, final_held +2.02 — generalizes.
date/quantity dips are 1-2 cases each (55-case categories).

Latency (with concurrent pack-build I/O contention): candidate generation mean
501 ms -> 553 ms (+10%); select overhead unchanged ~0.4 ms. Clean latency
re-measurement is deferred to the final phase.

## Gate
Mission gate "alias category recall improves": **PASS** (+45.6 strict, +45.6 lenient).

Artifacts: `phase3/frozen-weights-10k.json`, `phase3/fit-10k.json`,
`phase3/fitted-weights-10k.json`.
