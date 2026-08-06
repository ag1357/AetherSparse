# V08 Diagnostic — Where the Pipeline Loses 32 Points (DRAFT in progress)

Mission 3 deliverable. Branch `droid/diagnostic-v08`. All numbers strict
article recall unless noted; tiers 10k/25k/100k/397k; fit on
tuning+development only; frozen benchmark read-only.

## Scaling curve (final stack, transferred 10k weights)

| tier | strict | lenient | pool recall (strict) |
|---|---|---|---|
| 10k | 82.73% | 88.52% | (from phase6) |
| 25k | 81.17% | 87.19% | 88.52% |
| 100k | 75.39% | 82.03% | 82.42% |
| 397k | 67.42% | 75.55% | (phase6) |

Erosion per decade: −3.92 pp (10k→25k), −9.60 pp (25k→100k), −13.3 pp
(100k→397k): accelerating.

## Phase 1a — weight staleness

- 10k refit == shipped weights (by construction).
- 25k refit: +0.95 pp on fit partitions; eval 81.87% vs 81.17% transferred
  (+0.70 pp). Below the 2 pp gate.
- 100k refit: +0.95 pp fit-side; weights shift materially (f1 0.02→0.30,
  char3gram 0→0.20). Eval @100k: PENDING (eval100krefit).
- Gate: refit helps ≥2 pp only at 100k+ → per-tier weights in Mission 4 if
  the 100k eval confirms.

## Phase 1b — candidate budget (probe-scaled sweep)

First sweep was vacuous: `candidate_limit` never controlled probe depth
(lexical probe capped at 48). Added diagnostic `probe_scale` (dd32979).

@25k (scaled): k=96/192/384/768 → strict 81.17/81.64/81.64/81.56; pool
recall 88.52→92.03 (+3.5 pp); I/O +29%; p95 gen 3.29→4.60 s.

**Candidate budget is NOT binding at 25k**: 8× probes buy +0.47 pp strict.
The ranking stage loses 10.4 pp even with 92% pool recall. @100k sweep:
PENDING.

## Phase 1c — erosion shape (four-tier decomposition)

Weighted erosion shares (10k→397k): misspelling 22.4%, alias 19.4%,
direct_fact 12.2%, pronoun 12.2%, redirect 11.2%; multi-source
(two+three_to_six+comparison) 17.9% vs 23.4% of cases — NOT concentrated.
Gate FAILS: the fix is not per-entity budgets.

Bend points: alias/redirect collapse at 25k→100k (−27.8/−28.0 pp);
misspelling worsens every decade (−7/−12/−25); pronoun late (−3/−6/−15).

## Candidate-vs-ranking split by category (pool provenance)

| category | pool% 25k→100k | top1% 25k→100k | rankloss% 25k→100k |
|---|---|---|---|
| misspelling | 89→79 | 45→37 | 44→42 |
| alias | 89→61 | 79→38 | 10→23 |
| redirect | 92→62 | 78→48 | 14→14 |
| pronoun | 93→87 | 90→80 | 3→7 |
| direct_fact | 96→94 | 91→82 | 6→12 |
| two_source | 99→97 | 83→76 | 17→22 |
| three_to_six | 98→86 | 91→80 | 9→19 |
| comparison | 99→95 | 92→88 | 8→12 |

- alias/redirect/three_to_six: candidate-pool collapse at 25k→100k.
- misspelling: ranking stage loses ~43% at EVERY scale (gold in pool, never
  top-1). Constant, not scale-driven.
- two_source/direct_fact: ranking loss grows with scale.

## Phase 2/3 — oracle ladder @10k (200 cases, 130 answer)

| rung | strict | evidence | exact answer |
|---|---|---|---|
| 0 baseline | 84.62% | 76.15% | 32.31% |
| 1 +candidate | 93.08% (+8.46) | 83.08% | 33.08% |
| 2 +ranking | 100% (+6.92) | 85.38% | 35.38% |
| 3 +evidence | 100% | 100% | 40.00% |
| 4 +controller | 100% | 100% | 100% |

Stage attribution (88 failed answer cases): A=11, B=9, C=16, D=52, E=0.
Controller dominates the exact-answer metric (+60 pp marginal); candidate
and ranking split the article-recall headroom at 10k. 25k ladder: PENDING.

## Phase 4 — semantic channel as candidate generator

Sidecar: model2vec potion-base-8M → PCA 96d → int8 (recipe from Mission 2
Phase 2; quantization cost 0.00 pp there). Arms @100k: PENDING (sem100k).

## Phase 5 — calibration (margin feature, PAVA lookup, held partitions)

| signal | ECE @25k | ECE @100k | note |
|---|---|---|---|
| P(answerable) | 0.150 | 0.045 | 220/770 non-ANSWER cases carry literal
template markers (offcorpus/qorvax); excluding them: 0.111/0.097 |
| P(entity link correct) | 0.072 | 0.089 | usable |
| P(top-1 correct) | 0.141 | 0.097 | weak: max precision ~0.72 @100k |

Gate: no query-adaptive routing on P(top-1) — calibration not usable.

## Phase 6 — discourse carry

KEY FINDING: every V07 run used `discourse_boost=0.0` (carry OFF); V06 used
0.35. The 397k pronoun "regression" (72% vs V06 89%) is likely missing
carry, not a code regression.

Mechanism (carry-off per-case data): prior-turn top-1 accuracy 95% @25k →
89% @100k; strict|parent-correct 97.4%/94.4% vs strict|parent-wrong
70.0%/81.8%. Entrenchment risk is real and grows with scale.

Variants @25k (boost 0.35): none / margin-gated / compat-gated: PENDING.
@100k: PENDING.

## Decision label (preliminary)

Evidence so far points to a compound label:
`CANDIDATE_GENERATION_COLLISION_AT_SCALE` (alias/redirect/three_to_six pool
collapse) + `RANKING_UNDER_USE_OF_POOL` (misspelling 43% constant rankloss;
two_source/direct_fact growing rankloss; 8× probes buy +0.47 pp) +
`ANSWER_CONTROLLER_REALIZATION_GAP` (exact answer 32% at 10k with 85%
article recall; D dominates attribution).

Final label after Phase 3 @25k, Phase 4 @100k, Phase 6, and the Phase 7
397k confirmation.
