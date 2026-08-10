# Phase 4 Gate — Composition Operators (@10k, canonical leads)

Branch: `droid/controller-v09`. Baseline: Phase 3 final (edd96a5 lineage) —
mode-2 canonical 52.89%, mode-3 canonical 33.67%, disposition 87.85% / 75.12%.

## Branch check (pre-condition)

183/238 composition cases (76.9%) have ALL gold parts present in the mode-3
selected evidence (two_source 82.0%, three_to_six 67.2%, comparison 78.0%;
pool presence 99.2%). ≥60% → build composition operators (per user directive;
<30% would have meant per-entity reserved slots instead).

## Sub-mode taxonomy (238 cases)

| sub-mode | n | tractability |
|---|---|---|
| list:wrong_parts | 70 | slot claim choice — operator-built (4.1) |
| comparison:value_mismatch | 54 | pair choice + sign — operator-built (4.2) |
| list:missing_parts | 39 | claim absence — enumeration-bound, deferred |
| comparison:no_realization | 36 | pair absence — enumeration-bound, deferred |
| list:no_realization | 34 | all-or-nothing target miss — enumeration-bound, deferred |
| list:order_or_format | 4 | covered by 4.1 where slot-shaped |
| comparison:no_operator | 1 | — |

## 4.1 LIST slot-shape binding (2c384b7)

Mechanism (evidence: 'Using both sources, what are Brown and Sign?'): the
gold gloss claims exist in the graph (shape=definition, conf 1.00) but lose
per-slot to LIST-shaped extraction residue ('{{reflist}}', infobox tails,
conf 0.75) because _claim_fit scores shape_fit against the LIST container
(1.0 vs 0.2). Operator: when requested_relation_families maps to exactly
one shape, per-slot ordering recomputes the 0.21 shape_fit term against
that slot shape.

Measured: two_source +39.09 pp both modes (+43 cases); all other categories
0.00; disposition unchanged; **transfer 100%**. mode-2 52.89→56.25,
mode-3 33.67→37.03.

## 4.2 Comparison value-kind pairing (867ac67)

All 110 comparison questions are 'Compare the stated % values for X and Y.'
Three parts:

1. Value-kind pair filter: COMPARISON frames routed through
   _demanded_value_kind ('%' cue added); pairing restricted to the demanded
   kind when a two-subject typed pair exists (subject-viability guard —
   unguarded version cost −0.19 pp disposition).
2. compare_quantities surface_percent_compat: '%' in the surface is unit
   evidence when quantity_unit is empty on one side. Selection is two-pass
   (strict first, lenient fallback — always-on normalization changed pair
   order and cost −0.16 pp canonical); verification.py COMPARISON_DIRECTION
   recomputes with the same strict-then-lenient semantics (misaligned
   verification rejected lenient-selected answers: −0.30 pp disposition).
3. canonicalize strips a leading positive sign (gold never carries sign
   prefixes; negative sign stays semantic).

Measured: comparison +5.46 pp mode-2 / +3.63 mode-3 (+6/+4 cases); all
other categories 0.00; disposition 87.85→**88.15** mode-2 (+0.30), mode-3
unchanged; **transfer 66%**. mode-2 56.25→56.72, mode-3 37.03→37.34.

## Gate result

Composition class: 238 → 189 residual failures = **+20.6 pp of class**
(gate: +10 pp). Cumulative @10k: mode-2 canonical 52.89→**56.72** (+3.83),
mode-3 canonical 33.67→**37.34** (+3.67). **Phase 4 SHORT-ACCEPT
recommendation.**

Residual classes are enumeration-bound (claims/values absent or mangled at
extraction: '20128,%' vs gold '01.0162%'), which the user deferred with
Phase 5/6. No further Phase 4 iterations planned.

## Falsified / iteration record

- 4.2 unguarded kind filter: −0.19 pp disposition (typed pool < 2 subjects
  → abstain on answer-cases). Guard: require ≥2 distinct typed subjects.
- 4.2 always-on unit normalization: +0.25 pp disposition but −0.16 pp
  canonical (lenient pairs beat strict pairs on order). Fix: two-pass.
- 4.2 two-pass without verification alignment: lenient-selected answers
  failed COMPARISON_DIRECTION (verification re-ran strict only). Fix:
  mirror strict-then-lenient in verification.
