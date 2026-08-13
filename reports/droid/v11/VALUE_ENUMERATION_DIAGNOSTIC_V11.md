# Mission 6 value-enumeration diagnostic

## Result

`VALUE_ENUMERATION_DIAGNOSTIC_V11` is a split-safe Work-side partial
qualification over the certified Mission 5 residual. It covers 89
development/tuning tier replicas grouped into 34 unique case IDs. Evaluation
and final-held rows were excluded from every design, classification, model,
and threshold path.

The residual is not one failure mechanism:

| Proven/blocked class | Replicas |
|---|---:|
| compiler never extracted the exact target value | 27 |
| runtime extractor never extracted the exact target value | 30 |
| all target atomic values present but not correctly subject/relation bound | 11 |
| blocked by missing selected-chunk/pre-pruning state | 21 |

The 21 blocked replicas are not assigned a guessed cause. The minimal pack-side
continuation is documented in
`docs/reproduction/V11_VALUE_ENUMERATION_HANDOFF.md`.

## Deterministic value baselines

All candidate surfaces below are exact substrings of the supplied immutable
development/tuning evidence. The typed scan retains signed/unsigned and
grouped/dotted numeric alternatives as competing candidates rather than
rewriting a value.

| Baseline | Unique values enumerated | Unique values retained | Replica values retained |
|---|---:|---:|---:|
| A. current replay | 4/34 | 4/34 | 11/89 (12.36%) |
| B. typed scan before sentence pruning, cap 8 | 31/34 | 30/34 | 77/89 (86.52%) |
| C. shape-conditioned sentence boost, cap 8 | 31/34 | 30/34 | 77/89 (86.52%) |
| D. bounded late pruning, cap 64 | 31/34 | 31/34 (91.18%) | 80/89 (89.89%) |
| E. relation-conditioned rank, cap 8 | 31/34 | 30/34 | 77/89 (86.52%) |
| F. subject+relation+type binding, cap 8 | 31/34 | 30/34 | 77/89 (86.52%) |

Late pruning is the only retained deterministic improvement among B–F. Shape,
relation, and subject/type ordering do not improve the cap-eight result on this
small residual; this negative result is retained rather than assigning intuitive
weights.

The source-bound scan processed 19,908 bytes and proposed 128 candidates across
34 unique cases. A 101-trial Work-host microbenchmark (after 10 warmups) measured
2.249 ms median and 2.787 ms p95 per 34-case batch, equivalent to 0.066/0.082 ms
per case under this batch composition. The analytical relative P4 count is
21,956 character/candidate operations. It is not an MCU latency measurement.

## Typed value lattice and truth boundary

`TypedValueCandidate` now carries:

- exact source span and raw surface;
- canonical comparison form;
- value type;
- subject and relation hypotheses;
- time scope and unit;
- speaker attribution and section;
- document identity, confidence, and provenance.

Construction rejects any candidate whose raw surface, document, hash, or span
provenance does not match exact evidence. The lattice is bounded, preserves
competing candidates, and supports stable confidence-ranked merge/deduplication.

The compiler exposes typed matches before type caps, after type caps, and after
the page claim cap. The runtime exposes every scored region, all matches before
the top-eight region cut, matches before deduplication, and values before/after
deduplication and the four-value cap.

## Neural specialist decision

`NOT_TRAINED_INSUFFICIENT_EXACT_DEVELOPMENT_SPANS`

The frozen benchmark yields only 116 direct, uniquely located development
answer spans across supported non-comparison shapes. That is inadequate for a
lawful 0.5M-parameter span model. The deterministic residual is confined to
three tuning-only quotation cases with no failing development quotation member
in this residual family. Training on tuning or using held-out labels would
violate Mission 6. No factual free-generation model was created.
