# Phase 3 — Multi-document retrieval: diagnosis → dominant-cause fix

Date: 2026-08-04. Branch: `droid/semantic-v07`. Tier: 25k (gate), 10k
(ledger). Benchmark: V050 R1, gold by pageid.

## Gate result — PASS

`three_to_six_source` and `two_source` strict @25k, vs the Phase 2 clean
stack (`p2-clean-25k.json`):

| category | baseline | fixed | delta | gate |
|---|---|---|---|---|
| three_to_six_source | 42.50% | **63.75%** | **+21.25 pp** | ≥ +8 ✓ |
| two_source | 55.45% | **77.27%** | **+21.82 pp** | ≥ +8 ✓ |

Single-source regression check (limit −1 pp): every single-source category
delta ≥ 0.000 (largest collateral movements are improvements: follow_up
+8.0 pp, comparison +2.7 pp). Overall reranker strict @25k: 77.11% →
**81.17%** (+4.06 pp); fusion strict 76.80% → 80.94%.

## Diagnosis before fix (mission requirement)

`scripts/droid/multidoc_diagnose.py` replicates the harness flow with a
provenance spy on `store.search`, then classifies every missing gold pageid
of every failing multi-source case (95 pass / 95 fail of 190):

| cause | missing golds | share | meaning |
|---|---|---|---|
| **(b2) sub-query lexical miss** | **64** | **54%** | entity resolved, sub-query issued, gold absent even from its deep top-24 |
| (d) ranked out of top-8 | 38 | 32% | gold in the 96-pool, lost the global-score merge |
| (c) per-entity budget truncation | 14 | 12% | gold in the entity sub-search beyond the per-entity cut |
| (a) same-doc repetition | 2 | 2% | sub-query slots spent on one wrong doc |
| (b) entity unresolved | 1 | 1% | no anchor for the gold entity |

Follow-up depth probe on the 64 (b2) golds: **83% sit in their own entity
sub-query's top-200 at median depth 58** — the OR-of-terms sub-query is
buried by lexical collisions at 25k scale — and a **document_id-scoped title
MATCH retrieves the gold document's chunks in 62/62 cases (100%)**.

So the dominant cause is generation-side: not the splitter, not the merge —
the per-entity retrieval path. This is the same mechanism the Phase 2
post-mortem flagged for overall erosion (lexical collisions grow with corpus
size and push gold out of the candidate pool before ranking).

## Fix (dominant cause only)

In `candidates()`, multi-entity branch: each resolved entity anchor gets a
**reserved document-scoped probe** — `chunks_fts MATCH <title terms ANDed>
AND c.document_id = <entity doc>`, `max(3, 12//N)` slots per entity, unioned
into the pool before the existing sub-query share. Ranking is untouched;
single-entity queries are untouched.

## Results

25k (above) and 10k:

| category | baseline | fixed | delta |
|---|---|---|---|
| three_to_six_source | 42.50% | 61.25% | +18.75 pp |
| two_source | 62.73% | 78.18% | +15.45 pp |
| overall reranker strict | 79.53% | **82.50%** | +2.97 pp |

No category regressed at either tier.

## Notes

- (d) became survivable: with every entity's own document guaranteed pool
  presence, the global-score merge no longer starves weak entities (top-8 has
  room when each entity contributes 1-2 strong title-matching chunks).
- Residual multi-source gap (36% strict miss on three_to_six_source) is now
  dominated by cases with 4-6 gold docs where 8 slots simply cannot cover all
  entities plus context — a selected_limit structural ceiling, not a bug.
- Artifacts: `reports/droid/phase3/diagnose-25k.json`,
  `p3-10k-docprobe.json`, `p3-25k-docprobe.json`.
