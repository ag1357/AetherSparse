# Phase 3 Gate Record — SHORT-ACCEPTED (+13.98 pp vs +15 pp)

**Decision (user, 2026-08-10):** accept +13.98 pp; do not pursue TEMPORAL
binding or VALUE_NOT_ENUMERATED extraction.

**Reasoning (user):** Phase 3's +13.98 pp mode-2 transferred +0.39 pp to
mode 3 (transfer rate ~0.03). Further Phase-3-scope work transfers at the
same rate; the TEMPORAL option's +4.5 pp mode-2 headroom is worth roughly
+0.13 pp product. The +15 pp gate was written before the transfer rate was
known and is superseded.

## Phase 3 landed sequence (canonical mode-2 @10k, n=1280)

| step | change | canonical mode-2 | Δ |
|---|---|---|---|
| baseline | — | 38.91% | — |
| 3.1 | verified-answer bypass of unknown-mention ABSTAIN / uncertainty CLARIFY | 49.53% | +10.62 |
| 3.2 | value-kind binding + span-salience tiebreaks | 52.89% | +3.36 |
| 3.3 | gloss tiebreak | 52.89% | +0.00 (reverted) |

Disposition accuracy (all 2050): 79.27% → 87.85%. Three-mode table
(reports/droid/v09/shape-mode1-current.json, phase3-span-salience.json):
mode 1 == mode 2 == 52.11% surface / 52.89% canonical; mode 3 (product)
33.28% surface / **33.67% canonical**; mode-2→mode-3 drop 19.22 pp.

## TEMPORAL_SCOPE_WRONG +4 regression — diagnostic (cause only, no fix)

Diff of taxonomy-10k.json (pre-3.x) vs taxonomy-10k-current.json:
- **7 cases** reclassified VALUE_NOT_ENUMERATED → TEMPORAL_SCOPE_WRONG:
  classification artifact of the granularity-aware `canonical_match`
  (realized `2010-06-01` now matches accepted `2010`, so enumeration
  succeeds and the case reaches the selection-stage label). Same realized
  answers; deeper label, not a behavior change.
- **1 case** (v050r1-case:9ff47260…) newly wrong: realized 1784 vs accepted
  1775 — span-salience tiebreak (3.2) flipped the selected claim between
  two date claims tied on fit. −0.08 pp against 3.2's +3.36 pp; accepted
  trade-off.
- 4 pre-existing temporal cases resolved by 3.1/3.2.

Net: 53 → 57. Neither 3.1 nor 3.2 introduced a temporal-scope interaction
bug; +4 is 7 label moves + 1 tiebreak trade-off − 4 resolved.

## Falsified hypotheses (Lane C, recorded per directive)

- **Raw-surface trigram dual-normalization** for misspelling: 0/36
  displaced cases recoverable @25k/@100k. FALSIFIED.
- **Redirect-folding** as the misspelling-collapse mechanism: FALSIFIED
  (redirect rows do not carry the orthographic variants; the failing
  surfaces are true misspellings, not redirect titles).
- **Established instead:** 36/36 displaced misspelling cases have a
  vocabulary correction within Levenshtein distance 2 → bounded
  edit-distance ≤2 index built (src/aethersparse/selection/spelling.py).
