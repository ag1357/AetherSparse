# Phase 2 — Semantic channel: negative result, channel reverted

Date: 2026-08-04. Branch: `droid/semantic-v07`. Benchmark: V050 R1
(`c4a8f45b…`), 1280 answer cases, gold by pageid. Fits used tuning+development
only; the frozen partition was never touched for any decision.

## Verdict

**The semantic channel fails the Phase 2 gate and is reverted.** The mission
hypothesis — that a semantic ranking signal grows in value as the corpus
scales while lexical value shrinks — is **not supported** by the data at 10k
or 25k. The accelerating erosion therefore is not explained by the absence of
a semantic signal at the ranking stage.

## What was built (then reverted)

- Static embeddings: model2vec `potion-base-8M` (29,528 tokens × 256d), mean
  pooling, no transformer at query time (token lookup + average).
- Per-pack sidecar: PCA to 96d (fit on a 30k-chunk sample of the pack's own
  embeddings), L2-normalize, symmetric per-vector int8 quantize. Files:
  `<pack>.emb96.{f32.bin,int8.bin,scales.f32,pca.npz,json}`; pack untouched.
- New 15th feature `semantic_fit` = cosine(query, chunk), computed over the
  lexically generated candidate pool (memmap sidecar, per-query vector cache,
  integer-dot path for int8).
- Fusion weights refit and reranker retrained with the channel live.

Channel validity was verified before any fit: gold candidates mean cosine
0.304 vs non-gold 0.155 (n=1168/8214 tuning candidates); 48.8% vs 19.9% above
0.3. The channel carried real signal — it simply added no marginal value.

(A first fit silently used a dead feature — sidecar glob matched
`<pack>.sqlite.emb*` while the builder wrote `<pack>.emb*`; caught by the
gold-vs-other diagnostic showing all-zero features, fixed, both fits redone.)

## Measurements (banked in `reports/droid/phase2/`)

Fitted weights with the channel: fusion semantic_fit = **0.01** (fit-partition
strict 80.90 → 81.13%); reranker semantic_fit int8 weight = **12** (max 127).

| tier | config | fusion strict | reranker strict | reranker lenient |
|---|---|---|---|---|
| 10k | v07 semantic off | 79.61% | 79.30% | 87.89% |
| 10k | v07 semantic int8 | 79.77% | 79.22% | 87.97% |
| 10k | v07 semantic float | 79.77% | 79.22% | 87.97% |
| 25k | phase-1 config (baseline) | 76.17% | 74.30% | 83.52% |
| 25k | v07 semantic off | 76.80% | 76.64% | 86.09% |
| 25k | v07 semantic int8 | 76.80% | 76.48% | 86.09% |

**Channel delta: +0.16/−0.08 pp at 10k (fusion/reranker); +0.00/−0.16 pp at
25k. Gate was ≥ +4 pp strict @25k. Flat across tiers — the growth hypothesis
fails.** Per-category @25k (reranker strict, int8 vs off): every category
0.000 except three_to_six_source −0.025 (2 cases).

**Quantization cost: 0.00 pp** — float and int8 runs are identical at 10k
(per-vector scales make int8 cosine nearly lossless; sidecar-build fidelity
check: mean cosine 1.0000, n=2000).

**Budgets:** int8 sidecar 14.9 MB @10k (within the 20.2 MB PSRAM line) but
**36.9 MB @25k (over)** — an independent constraint failure at the gate tier.
CPU overhead was not cleanly measured solo (all benchmark runs executed under
4-way contention; contention-inflated candgen p50 ≈ 949 ms for all modes
including off shows no semantic-specific signal there). The recall gate alone
mandates the revert; the memory breach independently confirms it at 25k.

## Why the channel adds nothing (structural post-mortem)

The semantic feature scores only candidates that lexical generation (FTS5
BM25 + repair probe) already placed in the 96-slot pool. Within that pool,
lexical features already separate gold (title_overlap weight 127, alias 64,
char3gram 54 in the retrained reranker). A semantic channel without its own
retrieval path can only re-rank; it cannot rescue
semantically-relevant-but-lexically-absent chunks. The mission itself flagged
the alternative ("192d … requires SD+IVF"): value at scale would need an
ANN/IVF candidate-generation path over the embeddings, not a fusion feature.
That is a different architecture and a different budget conversation.

## Consequence for the erosion story

Erosion is accelerating (−6.70 then −10.60 pp/decade) and ranking-side
semantics do not move it. The remaining prime suspect is **candidate
generation**: lexical collisions growing with corpus size push gold chunks out
of the top-96 pool before any ranker sees them. This is measurable directly
(gold-in-pool rate per tier) and is the lens for Phase 3's multi-doc work and
the Phase 6 full-corpus validation.

## What is kept (the actual Phase 2 gain)

The channel is removed (feature, sidecar machinery, model2vec dependency,
harness flag), but a **clean 14-feature refit + retrain** is kept — refitted
and retrained from scratch without the feature, not truncated from the
15-feature tuples (the co-trained weights differ materially: char3gram int8
weight 54 co-trained vs 3 clean). Confirmed by full-benchmark runs:

| tier | phase-1 stack | clean v07 stack | delta |
|---|---|---|---|
| 10k | 76.80% | **79.53%** (lenient 88.36%) | **+2.73 pp** |
| 25k | 74.30% | **77.11%** (lenient 86.72%) | **+2.81 pp** |

Misspelling category: 32% (phase 1) → 82% @10k / 78% @25k — the retrained
reranker now exploits the repair probe's candidates. Kept model:
`reranker-v07c.int8.json`, identity
`019df0598b69a98f3006783e639aee053459aeb98d1ba143ec0763a83b235010`,
weights (3,127,60,-24,-119,-7,46,-27,3,16,3,-3,59,3). Fusion refit tag
`phase2-nochan-v1` (fit-partition strict 80.90%).
`c.rowid AS chunk_rowid` is kept in candidate queries (cheap provenance that
any future per-chunk sidecar would need).

Kept artifacts: `reports/droid/phase2/*.json` (all measurements above plus
`fit-nochan.json`, `p2-clean-10k.json`, `p2-clean-25k.json`).
