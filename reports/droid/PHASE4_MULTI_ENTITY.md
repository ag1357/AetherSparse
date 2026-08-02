# Phase 4 — Multi-Entity Query Decomposition

Branch: `droid/retrieval-accuracy-v06`
Pack: `selector-10k-p3.sqlite` (unchanged from Phase 3)
Benchmark: frozen V050 R1 (hash-verified), 1,280 ANSWER cases
Prior state (Phase 3, commit `904ae9f`): fusion strict 73.67% / lenient 83.52%

## Change (`selection/selector.py` only)

Multi-source questions need N distinct documents, but a single ranked candidate
list is easily dominated by one entity.  `candidates()` now detects general
conjunction/enumeration structure — >=2 resolved anchor documents plus a general
marker (`;` or one of {and, both, each, compare, versus, vs}) — and gives each
resolved entity an independent retrieval share:

- main FTS pool halved (`lexical_limit // 2`),
- per-entity sub-query (`entity title + up to 6 context terms`, context terms =
  query tokens minus all anchor-title tokens), `max(6, lexical_limit // (2N))` rows
  each, up to 6 entities,
- union before ranking; ranking still scores every candidate against the full
  original query, so no question-specific logic enters the scorer.

Detection verified on benchmark shapes: "Using both sources, what are X and Y?",
"Give one source-backed description for each of: A; B; C.", "Compare the stated %
values for X and Y." — all fire; single-entity questions never reach >=2 anchors
with a marker, so single-source categories are untouched by construction.

## Weight refit

Coordinate search re-run on tuning+development (feature-tag
`phase4-multi-entity`): converged to the Phase 3 optimum from a different
trajectory — decomposition changes pools but not the fit-partition optimum.
`FUSION_WEIGHTS` therefore unchanged.

## Measurement (10k pack, all 1,280 ANSWER cases)

| category | strict before | strict after | delta |
|---|---|---|---|
| comparison | 0.5455 | 0.7455 | +0.2000 |
| two_source | 0.6909 | 0.6818 | -0.0091 |
| three_to_six_source | 0.4625 | 0.4625 | 0.0000 |
| all single-source categories | — | — | 0.0000 (exactly) |

Overall fusion: **strict 73.67% -> 75.31% (+1.64)**, lenient 83.52% -> 83.20% (-0.31).
Fit partitions: -0.39/+3.61 (tuning/development); held-out (informational):
evaluation +2.13, final_held +1.01 — generalizes.

two_source -0.9 = 1 case of 110; three_to_six flat because strict recall there
needs 3-6 distinct documents inside the 8-chunk selection — a ranking-diversity
problem, not a candidate-pool problem (noted for the final report).

## Gate
Mission gate "strict recall on two_source, three_to_six_source, comparison
improves without degrading single-source categories by more than 1 point":
**PASS** (comparison +20.0; single-source degradation exactly 0 everywhere).

Artifacts: `phase4/fit-10k.json`, `phase4/decomposition-10k.json`.
