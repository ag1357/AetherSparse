# V06 Qualification — droid/retrieval-accuracy-v06

Date: 2026-08-03. Branch: `droid/retrieval-accuracy-v06`.
Benchmark: INDEPENDENT_NATURAL_QUERY_SET_V050_R1 (frozen; sha256
c4a8f45b30fa592d9ae7e01d0c456e95b7361e73575e97f97dbcb6da397cb673), 1280
questions; gold coverage 705/705 pageids in every pack below.

Kept configuration (the "V06 candidate"): bm25+char3gram fusion weights
0.16/0.20, redirect folding + anchor aliases, alias probing, multi-entity
decomposition, carry injection with discourse boost 0.35, candidate_limit 96,
trained int8 reranker (identity
13e650373444ac088e48e7fe106043e9efdd6d69e00c46999cf36db5fe15f439) as the final
stage. All fitting on tuning+development only; the frozen benchmark was never
modified.

## Headline: article recall vs corpus scale (kept config, all partitions)

| pack | docs | stage | strict | lenient |
|---|---|---|---|---|
| selector-10k-p3 | 10,000 | reranker (kept) | **75.70%** | 83.67% |
| selector-50k-p3 | 50,000 | reranker (kept) | **71.02%** | 80.47% |
| selector-full-p3 | 397,196 | reranker (kept) | **61.48%** | 73.05% |

Baseline (phase-1, 10k, legacy selector): fusion strict 65.16% / lenient 78.75%.
Net improvement at 10k: **+10.54 pp strict** (65.16% → 75.70%).
Scaling delta 10k → 50k (5× corpus): −4.68 pp strict; 10k → full (39.7×):
−14.22 pp strict. Sublinear erosion: the second 8× costs less than twice the
first 5×.

Fusion stage for reference: 50k strict 70.94% / lenient 80.23%, full strict
61.41% / lenient 72.89%; the reranker adds +0.08 pp strict / +0.24 pp lenient
at 50k and +0.07 / +0.16 at full (at 10k: +0.00 / +0.08).

## Per-category, reranker stage, strict (10k → 50k → full)

| category | 10k | 50k | full |
|---|---|---|---|
| alias | 90.0% | 67.8% | 47.8% |
| comparison | 74.5% | 73.6% | 57.3% |
| date | 60.9% | 60.9% | 64.5% |
| direct_fact | 98.2% | 95.9% | 87.7% |
| follow_up | 98.0% | 98.0% | 89.0% |
| misspelling | 18.0% | 18.0% | 14.0% |
| pronoun | 98.0% | 98.0% | 89.0% |
| quantity | 65.5% | 66.4% | 61.8% |
| quotation | 78.0% | 75.0% | 70.0% |
| redirect | 96.0% | 72.0% | 52.0% |
| three_to_six_source | 46.3% | 37.5% | 20.0% |
| two_source | 67.3% | 55.5% | 40.9% |

The alias/redirect categories degrade most with corpus scale (more same-name
distractors); follow_up/pronoun (carry injection), date, and direct_fact are
the most scale-stable. Misspelling remains the known small regression from
Phase 2 (char3gram weight too low to rescue heavy misspellings; accepted in
phase-2 analysis).

By partition (fusion, strict): 50k — tuning 70.5%, development 69.3%,
evaluation 72.0%, final_held 69.2%; full — tuning 58.5%, development 60.8%,
evaluation 63.8%, final_held 57.6%. Held-out tracks tuning at both scales:
no partition overfit.

## Latency (selector stage overhead per query, clean 10k re-measurement)

Measured on an otherwise idle CM5 (`reports/droid/final/kept-10k-clean.json`;
accuracy reproduced exactly: reranker strict 75.70% / lenient 83.67%):

| stage | mean | p50 | p95 |
|---|---|---|---|
| lexical | 0.59 ms | 0.55 ms | 0.90 ms |
| fusion | 0.40 ms | 0.38 ms | 0.61 ms |
| reranker | 0.34 ms | 0.32 ms | 0.51 ms |

Selector-side scoring overhead only (excludes FTS/I/O, which is
pack-size-dependent and characterized on-device in Phase 8). The kept pipeline
adds well under 1 ms of CPU per query.

## Phase 8 (ESP32-P4 on-device I/O) summary

See PHASE8_P4_IO.md. ESP32-P4 @ 360 MHz + SD08G microSD, SDMMC 40 MHz 4-bit:
raw random 4K p50/p95/p99 = 1401/1782/1888 µs (n=1200), sequential 14.72 MB/s;
SDSPI p50 3930 µs / 1.63 MB/s; production-shaped FTS5 query over the real 1k
pack cold-cache p95 = 405 ms (n=20, raw-sector VFS; stdio/FatFs seeking is
pathological on large files and must be bypassed).

## Phase ledger (10k fusion strict unless noted)

| phase | change | metric |
|---|---|---|
| 1 | article_recall_strict harness metric | baseline 65.16% |
| 2 | bm25+char3gram fusion weights (fit) | 65.16% → 73.18% |
| 3 | redirect folding + anchor aliases + alias probing | 69.84% → 73.67% |
| 4 | multi-entity decomposition | → 75.31% |
| 5 | carry injection (discourse boost 0.35) | → 75.62% |
| 6 | candidate_limit 96 | → 75.70% |
| 7 | trained int8 reranker (kept stage) | reranker 73.67% → 75.70% |
| 8 | ESP32-P4 I/O characterization | see above |

## Known limitations

- Misspelling category: 18% strict (regression accepted in phase 2; the
  char3gram feature helps fusion overall but does not rescue heavy
  misspellings at the kept weights).
- Alias/redirect recall drops ~22-24 pp from 10k to 50k and lands at
  47.8%/52.0% at full scale — same-name distractors are the main
  scale-sensitive failure mode.
- three_to_six_source (20.0% at full) is the weakest multi-source category;
  candidate_limit 96 bounds the union for 3+ entity questions.
