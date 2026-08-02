# Phase 1 — metric fix and fresh baseline

## Change

`src/aethersparse/controller/evaluation.py` (`evaluate_ablation`): added
`article_recall_strict` — `gold_docs.issubset(retrieved_document_ids)` —
alongside the existing lenient intersection metric, which is retained
unchanged for historical comparability. The strict metric is the primary
optimization target for the rest of this mission.

The same definitions are mirrored for the selector path in the mission harness
(`scripts/droid/v050_selector_eval.py`), matching gold at pageid granularity
over the selector's top-8 selected evidence.

Tests: **176 passed, 0 failed** after the change.

## Fresh baseline (rebuilt 10k pack, unpatched selector)

Harness: `scripts/droid/v050_selector_eval.py --pack selector-10k.sqlite`
(candidate_limit 64, selected_limit 8, bootstrap reranker). 1,280 ANSWER
cases; gold matching by pageid. Report:
`reports/droid/phase1/baseline-10k.json`.

| stage | lenient | strict | strict−lenient |
|---|---:|---:|---:|
| lexical (generation order) | 73.59% | 57.81% | −15.78 |
| deterministic fusion | 78.75% | 65.16% | −13.59 |
| reranker (bootstrap weights) | 78.75% | 65.08% | −13.67 |

The lenient→strict drop is −13.6 points overall, matching the mission's
predicted correction (−13.2 on the reconstructed testbed). Numbers below are
the baseline for every later delta; historical reports used a different corpus
and are not comparable.

### Baseline by partition (fusion stage)

| partition | n | lenient | strict |
|---|---:|---:|---:|
| tuning | 258 | 78.68% | 68.22% |
| development | 166 | 77.11% | 60.84% |
| evaluation | 658 | 78.42% | 64.29% |
| final_held | 198 | 81.31% | 67.68% |

Fit partitions (tuning+development, n=424): lenient 78.02%, strict 65.33%.
These are the decision numbers for keep/revert gates; held-out partitions are
reported for information only.

### Baseline by category (fusion stage, strict)

| category | n | lenient | strict |
|---|---:|---:|---:|
| direct_fact | 220 | 98.18% | 98.18% |
| follow_up | 100 | 95.00% | 95.00% |
| pronoun | 100 | 96.00% | 96.00% |
| quotation | 100 | 79.00% | 79.00% |
| quantity | 110 | 68.18% | 68.18% |
| date | 110 | 62.73% | 62.73% |
| redirect | 50 | 60.00% | 60.00% |
| alias | 90 | 40.00% | 40.00% |
| misspelling | 100 | 22.00% | 22.00% |
| comparison | 110 | 100.00% | 47.27% |
| two_source | 110 | 94.55% | 48.18% |
| three_to_six_source | 80 | 95.00% | 13.75% |

The strict metric exposes the multi-source failure mode the lenient metric hid:
comparison/two_source/three_to_six_source lose 45–52 points between lenient
and strict. Single-source weaknesses: misspelling (22%), alias (40%).

### Reference latency (host, 10k pack)

Candidate generation: mean 503 ms, p50 344 ms, p95 1,103 ms (CM5, warm cache,
per-query `candidates()` including anchor search and feature extraction).

## Expected-effect note

The strict number is much lower than the lenient one by construction. This is
a correction of an over-counting metric, not a regression.
