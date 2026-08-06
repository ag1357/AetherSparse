# V07 Qualification — droid/semantic-v07

Date: 2026-08-05. Branch: `droid/semantic-v07`.
Benchmark: INDEPENDENT_NATURAL_QUERY_SET_V050_R1 (frozen; sha256
c4a8f45b30fa592d9ae7e01d0c456e95b7361e73575e97f97dbcb6da397cb673), 1280
answer cases (2050 with all dispositions); gold by pageid. All fitting on
tuning+development only; the frozen benchmark was never modified.
Full-corpus runs executed on the tailnet s600 (4-core VM); pack and benchmark
sha256 verified identical to the Pi copies in every report JSON.

## Amended erosion arithmetic (Mission 2 §1 correction)

V06 described scaling erosion as sublinear. Per-decade normalization shows it
**accelerating**: V06 lost −6.70 pp/decade (10k→50k) then −10.60 pp/decade
(50k→397k), +58%. This framing is carried through all V07 scaling claims.

## Headline: legacy vs V06 vs V07 at full corpus (397,196 docs, reranker stage)

| stack | strict | lenient |
|---|---|---|
| legacy (e95110d) | 49.61% | 64.84% |
| V06 | 61.48% | 73.05% |
| **V07 (final stack)** | **67.42%** | **75.55%** |

**Phase 0 gate: passed.** V06's gap over legacy was +10.54 pp strict at 10k;
at 397k it is **+11.87 pp** — the earlier mission's gains did not evaporate
at scale, they grew slightly. V07 adds a further **+5.94 pp** over V06 at
397k (+17.81 pp over legacy).

## V07 scaling curve (final stack, reranker strict, per-decade)

| tier | docs | strict | lenient |
|---|---|---|---|
| 10k | 10,000 | **82.73%** | 88.52% |
| 25k | 25,000 | **81.17%** | 87.19% |
| 397k | 397,196 | **67.42%** | 75.55% |

Per-decade erosion:

- 10k → 25k: −1.56 pp over 0.40 decades = **−3.92 pp/decade** (V06: −6.70)
- 25k → 397k: −13.75 pp over 1.20 decades = **−11.45 pp/decade** (V06: −10.60)
- 10k → 397k overall: −15.31 pp over 1.60 decades = −9.57 pp/decade
  (V06: −14.22 pp, −8.89 pp/decade)

**Statement: erosion HELD, not flattened.** V07 flattened the first
half-decade substantially (−3.92 vs −6.70 pp/decade) but eroded slightly
faster in the last decade (−11.45 vs −10.60). Total absolute erosion is
within 1.1 pp of V06's (−15.31 vs −14.22 pp) while the curve sits +5.9 to
+7.0 pp higher at every tier; relative erosion is unchanged (18.5% vs 18.8%
of the 10k value lost by 397k). The fixes raised the curve; they did not
change its terminal slope.

## Per-category, reranker strict: V06 vs V07 at 397k

| category | V06 | V07 | delta |
|---|---|---|---|
| alias | 47.8% | 47.8% | 0.0 |
| comparison | 57.3% | 69.1% | +11.8 |
| date | 64.5% | 64.5% | 0.0 |
| direct_fact | 87.7% | 87.3% | −0.4 |
| follow_up | 89.0% | 95.0% | +6.0 |
| misspelling | 14.0% | 35.0% | **+21.0** |
| pronoun | 89.0% | 72.0% | **−17.0** |
| quantity | 61.8% | 61.8% | 0.0 |
| quotation | 70.0% | 71.0% | +1.0 |
| redirect | 52.0% | 52.0% | 0.0 |
| three_to_six_source | 20.0% | 42.5% | **+22.5** |
| two_source | 40.9% | 72.7% | **+31.8** |

The three Mission 2 fixes show up exactly where they aimed: misspelling
(Phase 1), multi-source (Phase 3). **Known regression: pronoun −17 pp at
397k** (72/100 vs V06's 89/100; at 25k V07 holds 93%). Pronoun questions
depend on carry-injected context competing against a much larger distractor
pool at full scale; the per-case JSON lacks candidate-pool provenance, so
generation-vs-ranking attribution is deferred to follow-up (flagged for
Mission 3).

## Phase ledger

| phase | change | strict delta @10k | strict delta @25k | kept? |
|---|---|---|---|---|
| 1 | orthographic repair probe in candidate generation (edit-distance-1, corpus-DF probes) | 75.70 → 76.80% (+1.10) | (phase-1 config @25k: 74.30%) | kept |
| 2 | semantic channel (model2vec potion-base-8M, 96d PCA, int8) | −0.08 pp | −0.16 pp | **reverted** (gate +4 pp failed) |
| 2 | clean 14-feature refit + retrain (side effect of the phase) | 76.80 → 79.53% (+2.73) | 74.30 → 77.11% (+2.81) | kept |
| 3 | reserved document-scoped per-entity probe | 79.53 → 82.50% (+2.97) | 77.11 → 81.17% (+4.06) | kept |
| 6 | final refit+retrain on post-phase-3 code (v07d, identity b3ec6125…) | 82.50 → 82.73% (+0.23) | 81.17 → 81.17% (+0.00) | kept |

## Semantic channel (Phase 2) — negative result, recorded

Hypothesis: semantic value grows with corpus size while lexical value
shrinks. Measured contribution of the channel (same weights, on vs off):

| tier | fusion strict delta | reranker strict delta |
|---|---|---|
| 10k | +0.16 pp | −0.08 pp |
| 25k | +0.00 pp | −0.16 pp |

Flat, not growing → **the lexical-only erosion diagnosis is not supported**.
Gate (≥ +4 pp strict @25k) failed; channel reverted per mission. Float vs
int8 quantization cost: **0.00 pp** (identical recall @10k). Memory: int8
sidecar 14.9 MB @10k (within the 20.2 MB PSRAM line) but 36.9 MB @25k
(over) — a second, independent constraint failure. Structural post-mortem: a
semantic feature that only re-ranks the lexically generated pool cannot
rescue semantically-relevant-but-lexically-absent candidates; that needs an
ANN/IVF retrieval path (see static-index spike). Full writeup:
reports/droid/PHASE2_SEMANTIC.md.

## Multi-source failure-cause distribution (Phase 3, 25k, 95 failing cases)

| cause | missing golds | share | fix applied |
|---|---|---|---|
| (b2) sub-query lexical miss | 64 | 54% | **yes — dominant** |
| (d) ranked out of top-8 | 38 | 32% | no (survivable after generation fix) |
| (c) per-entity budget truncation | 14 | 12% | covered by the same fix |
| (a) same-doc repetition | 2 | 2% | no |
| (b) entity unresolved | 1 | 1% | no |

Dominant-cause fix: reserved document-scoped probe per resolved entity
(`chunks_fts MATCH title-terms AND c.document_id=…`, max(3, 12//N) slots).
Evidence: 83% of (b2) golds sat at median depth 58 in their own entity
sub-query's results; the doc-scoped probe retrieves them 62/62. Result:
three_to_six_source +21.25 pp, two_source +21.82 pp @25k (gate ≥ +8 pp,
passed), zero single-source regressions. reports/droid/PHASE3_MULTIDOC.md.

## Selective-answering confidence (all-dispositions run @397k)

Reranker top-1 score and top1−top2 margin by disposition (n=2050):

| disposition | n | top1 mean | top1 p50 | margin mean | margin p50 |
|---|---|---|---|---|---|
| ANSWER | 1280 | 1.007 | 1.027 | 0.051 | 0.0181 |
| CLARIFY | 330 | 0.895 | 0.934 | 0.061 | 0.0138 |
| ABSTAIN | 220 | 0.939 | 0.932 | 0.016 | 0.0010 |
| INCORRECT_PREMISE | 110 | 1.041 | 1.050 | 0.013 | 0.0005 |
| OUT_OF_CORPUS | 110 | 0.971 | 0.971 | 0.003 | 0.0026 |

The **margin** carries the selective-answering signal: answerable cases
(ANSWER/CLARIFY) show p50 margins 14-36× larger than non-answerable ones
(ABSTAIN/INCORRECT_PREMISE/OUT_OF_CORPUS). Top-1 score alone does not
separate (INCORRECT_PREMISE scores highest of all). A margin threshold near
0.005-0.01 is the candidate abstention gate for the assistant layer.

## Storage tuning (Phase 4, P4 + SD08G microSD, n=1200)

| config | p50 | p95 | p99 |
|---|---|---|---|
| 40 MHz 4-bit SLOT_1 (shipped; repro) | 1405 µs | 1781 | 1869 |
| 20 MHz clock | 1544 µs | 1903 | 1986 |
| 512 B random read | 1183 µs | 1445 | 1481 |
| 16 KB random read | 2125 µs | 2454 | 2602 |
| 64 KB random read | 4705 µs | 5035 | 5156 |

Verified: SLOT_1 4-bit (only usable slot), 40 MHz already in use, CMD18
already issued for multi-sector reads, `dma_aligned_buffer` reserved for
SDIO (not applicable), no per-transfer heap allocation in the driver.
**Best-achievable microSD random 4K p50 = 1401 µs** — the card's internal
random latency (~1.18 ms) is the floor; software recovered nothing because
nothing was misconfigured. 1401 > 800 µs → **eMMC justified**: expected gain
controller random latency ~3-5× (est. p50 340-610 µs); bus-width gain NOT
available (TF interface wires only 4 data lines).
reports/droid/PHASE4_STORAGE.md.

## Static index spike (Phase 5, design only)

docs/architecture/STATIC_INDEX_SPIKE.md: FST dictionary in PSRAM (6-15 MB at
full corpus), impact-ordered delta+varint postings in one contiguous
4K-aligned file, BlockMax WAND early termination, raw sector reads via the
existing rawsd path. Estimated **~6-12 block reads per query vs the current
~200-300 (~25×)**; on-device query **p95 ≈ 50-70 ms** (vs measured FTS5
405 ms), ~15-25 ms with eMMC. Engineering cost ~1.5-2 weeks. Recommended as
Mission 3.

## Next bottleneck (explicit)

Candidate generation at full corpus scale. Both Mission 2 wins (repair probe,
doc-scoped probe) were generation-side; the semantic ranking channel added
nothing; and the (b2) diagnosis showed gold documents buried at median depth
58 in their own entity's lexical results at 25k. Lexical collisions grow
with corpus size, and the 96-slot pool is where recall is now won or lost.
Concrete follow-ups, in order: (1) static index (Mission 3 candidate,
above); (2) pronoun regression at 397k (−17 pp vs V06) — needs
candidate-pool provenance in the harness to attribute; (3) alias/redirect
same-name distractors (47.8%/52.0% at 397k, unmoved by both missions).
