# V08 Diagnostic — Where the Pipeline Loses 32 Points

Mission 3 deliverable. Branch `droid/diagnostic-v08`. Strict article recall
(gold ⊆ selected top-8) unless noted. Tiers 10k/25k/100k/397k. Fit on
tuning+development only; frozen benchmark read-only; oracles never shipped.

## Decision label

**PRIMARY: `CANDIDATE_GENERATION_COLLISION_AT_SCALE`** — the lexical
candidate generator loses gold documents to title/alias collisions as the
corpus grows; this is the only stage whose failure rate grows with scale.

**SECONDARY: `RANKING_UNDERUSE_OF_POOL`** — the fusion/reranker leaves
~10-11 pp of pool recall unconverted at every tier, with a constant ~43%
loss on misspelling at all scales.

**TERTIARY (answer-level, scale-invariant): `CONTROLLER_REALIZATION_GAP`** —
exact-answer accuracy is 32-40% even with perfect retrieval and evidence;
the controller oracle is worth +60 pp at every tier.

## Scaling curve (final stack, transferred 10k weights, carry off)

| tier | strict | lenient | pool recall |
|---|---|---|---|
| 10k | 82.73% | 88.52% | 89.7% |
| 25k | 81.17% | 87.19% | 88.52% |
| 100k | 75.39% | 82.03% | 82.42% |
| 397k | 67.42% | 75.55% | — |

Erosion accelerates: −3.9, −9.6, −13.3 pp/decade.

## Phase 1 — cheap diagnostics (all gates resolved)

- **1a weight staleness: NOT the cause.** Refits: 25k +0.70 pp eval,
  100k +0.31 pp eval (gate ≥2 pp fails). The 100k refit moves real weight
  (f1 0.02→0.30, char3gram 0→0.20) and still buys nothing. Ship transferred
  weights; no per-tier retuning in Mission 4.
- **1b candidate budget: NOT binding.** First sweep was vacuous
  (`candidate_limit` never controlled probe depth; lexical probe capped at
  48 — fixed via diagnostic `probe_scale`, dd32979). Scaled probes: @25k
  8× → +0.47 pp strict (pool +3.5); @100k 4× → +1.64 pp (pool +5.9) at
  p95 20.7 s (over budget). More of the same candidates is not the fix;
  WHICH candidates is.
- **1c erosion shape: NOT multi-source.** Weighted erosion shares
  (10k→397k): misspelling 22.4%, alias 19.4%, direct_fact 12.2%, pronoun
  12.2%, redirect 11.2%; multi-source 17.9% vs 23.4% of cases. Bend points:
  alias/redirect collapse at 25k→100k (−27.8/−28.0 pp); misspelling
  worsens every decade; pronoun late.

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

alias/redirect/three_to_six = candidate-pool collapse; misspelling =
constant ranking-stage loss; two_source/direct_fact = growing ranking loss.

## Phase 2/3 — oracle ladders (Lane B harness, oracles never persisted)

Marginal strict-recall gains by tier:

| oracle | 10k | 25k | 100k |
|---|---|---|---|
| +candidate | +8.46 | +9.77 | **+14.61** |
| +ranking | +6.92 | +9.06 | (~+10, from rung1 B=128) |
| +evidence | 0 | 0 | — |
| +controller (exact-answer) | +60.0 | +60.5 | (rung4 closes to 100%) |

The candidate-oracle marginal is the only one that GROWS with scale.
Ladders close to 100% at every tier; E_BENCHMARK_DEFECT = 0 at 10k/25k/100k.

Rung-0 attribution: 10k D=52/A=11/B=9/C=16; 25k D=500/A=147/B=94/C=111.
A (candidate-missing) tripled 10k→25k.

Exact-answer with perfect retrieval+evidence: 40% @10k, 39.5% @25k —
the controller gap is scale-invariant. D-case characterization @10k:
73% wrong-value extraction from correct evidence, 21% empty, 6% formatting.

## Phase 4 — semantic channel as candidate generator

Sidecar: model2vec potion-base-8M → PCA 96d → int8 (Mission 2 recipe;
quantization cost 0.00 pp there). Arms (lexical arm reproduces the baseline
exactly at both tiers: 82.73% @10k, 75.39% @100k):

| arm | strict @10k | strict @100k |
|---|---|---|
| lexical_only | 82.73% | 75.39% |
| semantic_only | 60.16% | 50.23% |
| **union** | **84.53% (+1.80)** | **78.20% (+2.81)** |
| rrf | 79.06% | 74.84% |
| margin_gated | 82.73% | 75.86% |

Union gain GROWS with scale. Per-category @100k: **alias +17.8 pp**
(58.9→76.7%), quotation +7.0, quantity +4.6, direct_fact +3.2; redirect
+0.0 (redirect names absent from doc text); three_to_six −2.5 (pool
dilution); misspelling −1.0. Semantic-only recovers 72 lexical misses @100k.

## Phase 5 — calibration (margin, PAVA lookup, held partitions)

| signal | ECE @25k | ECE @100k |
|---|---|---|
| P(answerable) | 0.150 / 0.111 ex-markers | 0.045 / 0.097 ex-markers |
| P(entity link) | 0.072 | 0.089 |
| P(top-1 correct) | 0.141 | 0.097 |

220/770 non-ANSWER cases carry literal template markers (offcorpus/qorvax);
excluding them, answerability ECE ≈ 0.10. P(top-1) max precision ~0.72 @100k
→ **gate: no query-adaptive routing on margin** (mission gate stands).

## Phase 6 — discourse carry

KEY FINDING: all V07 runs used `discourse_boost=0.0` (carry OFF); V06 used
0.35. The 397k pronoun "regression" (72% vs V06 89%) is missing carry, not
a code regression.

Mechanism (carry-off data): parent top-1 accuracy 95% @25k → 89% @100k;
strict|parent-correct 97.4%/94.4% vs strict|parent-wrong 70.0%/81.8%.

Variants (boost 0.35):

| variant | strict @25k | pronoun | strict @100k | pronoun |
|---|---|---|---|---|
| off | 81.17% | 93% | 75.39% | 87% |
| fixed | 81.41% | 97% | 76.09% | 96% |
| margin-gated | 81.41% | 97% | 76.09% | 96% |
| **compat-gated** | **81.48%** | **98%** | **76.17%** | **97%** |

follow_up 99% in all variants — no entrenchment cost at ≤100k. Compat gate
(boost only when the question is anchorless or the carried doc is an
anchor) is the best variant at both tiers; margin gate is near-vacuous
(parents rarely fall below τ).

## Phase 7 — 397k confirmation (one run)

Config: transferred 10k weights + compat-gated carry 0.35 + shipped probes.
RUNNING. Prediction: ~68.2-68.6% strict (67.42% + carry), pronoun ~80%.

## Mission 4 fix directions (ranked by measured expected value)

1. **Semantic-union candidate generation** (+2.81 pp @100k, growing;
   alias +17.8 pp). Needs category-aware mixing to avoid three_to_six
   dilution (−2.5 pp). Redirect cases need a different channel (redirect
   names are absent from doc text — an explicit redirect/alias edge index).
2. **Ranking-stage misspelling repair** (constant ~43% rankloss at ALL
   scales = ~22% of total erosion). The features exist (char3gram); the
   ranking stage never promotes repaired candidates to top-1.
3. **Compat-gated carry 0.35** (+0.78 pp @100k, pronoun +10 pp) — ships in
   the Phase 7 config.
4. **Controller extraction** (+60 pp exact-answer ceiling at every tier;
   73% of D cases are wrong-value extraction from correct evidence).
   Scale-invariant but the largest single answer-level lever.

## Anti-goals confirmed

- Per-tier weight retuning: dead end (1a).
- Bigger candidate budgets of the same probes: dead end (1b).
- Per-entity budget fixes for multi-source: wrong target (1c).
- Query-adaptive routing on margin: blocked by calibration gate (Phase 5).
- Oracles: never persisted, never shipped (Lane B harness design).
