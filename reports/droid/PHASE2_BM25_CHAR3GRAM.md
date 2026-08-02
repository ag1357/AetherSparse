# Phase 2 — BM25 magnitude + char-3gram channels

## Change

Applied `bm25_char3gram.patch` (mission attachment) with one structural
addition: fusion weights live in a module-level `FUSION_WEIGHTS` constant so
the fitter and the shipped value share one source of truth.

- `feature[6]`: `semantic_similarity` (blake2s Jaccard, dead) → `bm25_score`:
  SQLite FTS5 `bm25()` (negative, lower-better) inverted and min-max scaled to
  [0,1] across the candidate set; supplemental/traversal candidates keep 0.0.
- `feature[13]`: `source_independence` (constant 1.0, dead) → `char3gram_fit`:
  integer cosine over character trigrams of query vs chunk body.
- Vector length unchanged (14); `CandidateScore` and the serialized model
  schema remain valid. Tests: **176 passed**.

## Weight refit (tuning+development only)

`scripts/droid/fit_fusion.py`, objective = strict recall, lenient tie-break,
candidate features cached (`fit-cache-phase2.pkl`, tag `phase2-bm25-char3gram`).

Coordinate search is start-dependent on this landscape; three starts were
fitted (all on fit partitions only):

| start | strict | lenient | bm25 weight |
|---|---:|---:|---:|
| patch-shipped weights | 72.88% | 80.42% | 0.00 (local optimum) |
| uniform 1/14 | 72.41% | 79.72% | 0.07 |
| bm25-heavy | **73.35%** | **80.66%** | 0.16 |

The bm25-positive optimum is both the best fit-partition result and the one
that keeps the phase's mechanism alive, so it was selected. A refinement pass
from that point made no further improvement (converged).

Fitted `FUSION_WEIGHTS`:
`(0.12, 0.40, 0.50, 0.05, 0.25, 0.05, 0.16, 0.05, 0.00, 0.40, 0.16, 0.50, 0.05, 0.20)`
(bm25_score 0.16, char3gram_fit 0.20; category_overlap 0.00).

Feature sanity check on fit queries: bm25_score mean 0.745 on gold candidates
vs 0.445 on non-gold (carries signal). char3gram_fit mean 0.158 gold vs 0.172
non-gold at chunk level — weak/inverted as a standalone signal, kept at fitted
weight 0.20; its contribution is re-examined if a later phase regresses.

## Result (10k pack, fusion stage, vs Phase 1 baseline)

| partition | strict | Δ | lenient | Δ |
|---|---:|---:|---:|---:|
| tuning | 77.52% | +9.30 | 82.17% | +3.49 |
| development | 66.87% | +6.02 | 78.31% | +1.20 |
| **fit combined** | **73.35%** | **+8.02** | **80.66%** | **+2.64** |
| evaluation (info) | 67.48% | +3.19 | 79.48% | +1.06 |
| final_held (info) | 70.20% | +2.53 | 80.81% | −0.51 |
| overall | 69.84% | +4.68 | 80.08% | +1.33 |

Gate: fit-partition strict improvement **+8.02 ≥ +5** → KEEP. Held-out strict
also improves (+3.19 / +2.53); the fit-to-held-out gap (~5 points) is the
price of coordinate search on 424 cases and is recorded, not tuned away.

Per-category strict movers: three_to_six_source 13.75%→43.75% (+30.0),
two_source +17.3, redirect +16.0, comparison +7.3, alias +4.4.
Regression: misspelling 22.0%→18.0% (−4.0) — noted; Phase 3 alias work and
the char-3gram channel are the intended remedies and are measured next.

Reranker stage (bootstrap int8 model on the new feature semantics) is now
slightly below fusion (strict 68.98% vs 69.84%) — the stale-weight effect the
mission predicts; Phase 7 retrains it.

Reports: `reports/droid/phase2/fit-10k.json`, `reports/droid/phase2/eval-10k.json`.
