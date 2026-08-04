# Phase 1 — Misspelling regression ablation (V07)

Branch: `droid/semantic-v07`. Pack: selector-10k-p3 (10,000 docs) +
selector-10k-p3-nofold (ablation variant). Benchmark: frozen V050 R1,
`tuning` partition for fitting/diagnosis, all partitions for the gate.
Prior state (V06 kept): reranker strict 75.70% / lenient 83.67%,
misspelling 18.0% strict.

## Ablation result (tuning, fusion stage, strict)

| variant | char3gram weight | redirect folding | misspelling | overall |
|---|---|---|---|---|
| A | 0.00 | off | 13.6% | 75.19% |
| B | 0.20 | off | 13.6% | 75.58% |
| C | 0.00 | on | 13.6% | 77.91% |
| D | 0.20 | on (current) | 13.6% | 78.29% |

Weight sweep on tuning (folded pack): 0.20 / 0.35 / 0.50 / 0.65 / 0.80 —
misspelling flat at 13.6% at **every** weight; overall flat then slightly
down at 0.80 (78.29% → 77.91%).

- **H1 (char3gram weight too low): REJECTED.** Misspelling recall is
  weight-independent across 0.0–0.80.
- **H2 (redirect folding destroys the fuzzy-match surface): REJECTED.**
  B == D exactly; folding changes redirect recall (46.7% → 93.3%, the phase-3
  win) but has zero effect on misspelling.

## Root cause (neither hypothesis)

Per-case diagnosis: the gold document is present in the candidate pool for
only **5/22** tuning misspelling cases. Candidate generation is exact-match
FTS; one orthographic error in a content term (the benchmark's pattern is an
adjacent transposition: rGammar→grammar, nIvention→invention, iFction→fiction)
removes the gold document before any ranking feature — char3gram included —
can act. The channel was never broken; it never saw the candidates.

## Fix: orthographic repair probe (candidate generation)

`_spelling_repairs()` in `selector.py`: query terms (len>2, non-stopword)
with **zero corpus occurrences** (indexed FTS EXISTS probe, ~54 µs) get
Damerau edit-distance-1 variants (transpositions first, then deletions,
substitutions, insertions), each checked against the corpus vocabulary; the
first confirmed variant per term (≤2 terms, ≤600 probes per query) runs as a
bounded 12-row candidate probe together with the query's known terms — same
pattern as the phase-3 expansion probe, never displacing the primary term
budget. General mechanism, no question-specific rules.

Pool presence for tuning misspelling cases: **5/22 → 19/22**.

## Gate measurement (10k, all partitions, kept config + repair)

Reranker stage, strict, per category vs V06 kept:

| category | V06 | V07 | delta |
|---|---|---|---|
| misspelling | 18.0% | **32.0%** | **+14.0** |
| all others (11) | — | — | **0.0 each** |

Overall: reranker strict 75.70% → **76.80%** (+1.10 pp), lenient 83.67% →
84.77%. Fusion strict 75.70% → 77.11%.

**Gate: PASS** (misspelling ≥ 18% restored and exceeded; no category
regressed > 1 pp — none regressed at all).

## Latency

Same-process A/B on 120 tuning queries: candidate generation p50 498 ms →
526 ms (+28 ms) with the probe; it fires on ~3% of queries (4/120). The
generation stage is FTS-bound (~500 ms p50 at 10k); the probe overhead is
~5% of it. (Run-to-run FTS variance between separate processes is larger
than the probe cost.)

## Notes

- The reranker (trained on pre-repair pools) now trails fusion slightly
  (76.80% vs 77.11%); retraining happens in Phase 2 with the semantic
  channel added, so fusion weights are not refit here.
- Builder: `ingest_mediawiki(..., fold_redirects=False)` ablation toggle +
  `--no-fold-redirects` CLI flag (used for variants A/B; kept for future
  ablations). Harness: `--char3gram-weight` override knob.
- Artifacts: `reports/droid/phase1/ablation-{A,B,C,D}.json`,
  `sweep-{0.35,0.50,0.65,0.80}.json`, `repair-10k.json`.
