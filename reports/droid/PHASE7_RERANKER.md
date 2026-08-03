# Phase 7 — Reranker retraining (V050 tuning+development)

## Change

Replaced the deterministic bootstrap reranker with a model actually trained on
the benchmark's tuning+development partitions:

1. **Training questions**: `scripts/droid/build_training_questions.py` projects
   the frozen V050 tuning+development questions onto the Phase 3 selector pack
   (pageid+offset gold matching; 424 questions built, 0 dropped; the frozen
   benchmark itself is never touched).
2. **Training**: `aethersparse selection train` (pairwise logistic over the 14
   Phase 2 features, int8-quantized) with `candidate_limit` at the new default
   of 96. 264 usable questions → 2112 pairs → 24 epochs.
3. **Embedding**: the trained weights replace `DEFAULT_MODEL` in
   `selector.py`; the model + training manifest are committed under
   `data/models/` (`evidence_reranker.int8.json`,
   `evidence_reranker.training.json`).

Model identity: `13e650373444ac088e48e7fe106043e9efdd6d69e00c46999cf36db5fe15f439`
int8 weights `(10, 127, 74, -21, -116, 8, 1, -29, 3, 6, 10, -3, 46, 23)`,
scale 0.0518058, bias 0. The model learned to lean on `title_overlap` (127) and
`alias_fit` (74) and to distrust `entity_fit` (-116) — consistent with the
Phase 3 alias/redirect repairs the bootstrap weights predated.

## Measurement (10k pack, candidate_limit=96, discourse boost 0.35)

Reranker stage, strict article recall:

| stage | strict | lenient |
|---|---|---|
| reranker (bootstrap, phase-6 kept state) | 73.67% | 82.66% |
| reranker (retrained) | **75.70%** | **83.67%** |
| fusion (same run, for reference) | 75.70% | 83.59% |

Per category (reranker stage, strict, phase-6 → phase-7):

| category | before | after | delta |
|---|---|---|---|
| redirect | 80.00% | **96.00%** | +16.00 |
| alias | 82.22% | **90.00%** | +7.78 |
| comparison | 66.36% | **74.55%** | +8.18 |
| two_source | 64.55% | 67.27% | +2.73 |
| three_to_six_source | 43.75% | 46.25% | +2.50 |
| misspelling | 21.00% | 18.00% | −3.00 (1.5 cases; known small regression) |
| direct_fact / follow_up / pronoun | 98.00% | 98.00% | 0 |
| date / quantity / quotation | — | — | 0 |

Held-out partitions (informational only; fitting used tuning+development):

| partition | before | after |
|---|---|---|
| evaluation | 74.62% | 75.99% (+1.37) |
| final_held | 72.73% | 74.24% (+1.52) |

The retrained reranker closes the gap to fusion on strict (75.70% = 75.70%)
and edges past it on lenient (83.67% vs 83.59%); the reranker stage is now the
best final stage and is the kept configuration.

## Selective answering (coverage table)

Per-case confidence dump: `reports/droid/phase7/per-case-10k.json` (1280
cases, reranker stage). Ranking by the top-1 score is far more discriminative
than ranking by the top1−top2 margin:

| coverage (answer top X% confident) | strict recall | lenient recall |
|---|---|---|
| 10% | **100.0%** | 100.0% |
| 25% | 92.2% | 96.3% |
| 50% | 89.7% | 97.5% |
| 75% | 88.0% | 97.5% |
| 100% | 75.7% | 83.7% |

(By margin instead: 82.0 / 88.4 / 69.5 / 73.3 / 75.7 — non-monotonic, weak
signal; margins are tiny, p50 ≈ 0.024.)

Interpretation: an abstention policy keyed on the reranker's top-1 score can
answer half the benchmark at ~90% strict recall, or the top decile at 100%.

## Artifacts

- `reports/droid/phase7/retrained-10k.json` — full three-stage report
- `reports/droid/phase7/per-case-10k.json` — per-case margins/scores
- `data/models/evidence_reranker.int8.json` + `.training.json` — model + manifest
- training questions: `work/artifacts/training-questions-10k-p3.json` (outside git)

## Test gate

176 passed (unchanged).
