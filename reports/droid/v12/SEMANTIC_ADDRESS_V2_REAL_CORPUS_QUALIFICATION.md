# Semantic Address v2 real-corpus qualification

Status: **QUALIFIED — STRICT POLICY GATE OPEN**

The selected working address model is a deterministic, zero-learned-parameter
query-span character index over the real 397k canonical address substrate. It
reaches **30/31 (96.77%) tuning candidate completeness at K=8/16/32** and a fresh
strict replay reaches **572/695 (82.30%)**, up from the published 324/695 baseline.
The required `>418/695` and `>60%` policy gate is open.

This is not a proxy-only result. The input is the pinned public Factory revision
`fc720346ec8bef82c0127efc43b377f0ded4d526`: 397,196 documents, 275,989 canonical
entities, 1,334,801 exact surfaces, and 7,627,708 hyperlink occurrences. All 51
authoritative payloads matched the outer manifest.

## Validated integration

The four Factory commits applied cleanly. A strict follow-up repaired their
registry-cache typing declaration, and the 10k smoke exposed one additional narrow
normalization defect: five en-dash titles were reachable under the raw Unicode title
but not the pack-authoritative ASCII-hyphen lookup. The exact adapter now emits both
spellings without changing the global lossless normalizer.

- 10k streams and all five reconstructed benchmark outputs verify byte-for-byte.
- Raw-title reachability: 7,455/7,455.
- Pack-normalized reachability: 7,450/7,455 before repair; 7,455/7,455 after repair.
- Addressing test suite: 51 passed.
- Ruff, strict addressing mypy, and `git diff --check`: passed.
- `LICENSE` and `NOTICE`: unchanged.

## Real 397k baseline and pivot

The untouched Factory capture is genuinely weak on the 50 authenticated exact
mention/entity alignments:

| System | R@1 | R@4 | R@8 | R@16 | R@32 |
|---|---:|---:|---:|---:|---:|
| Factory title/redirect/alias/anchor union, combined descriptive | 20.00% | 20.00% | 20.00% | 24.00% | 24.00% |
| Selected development | 21.05% | 89.47% | 100.00% | 100.00% | 100.00% |
| Selected tuning | 19.35% | 90.32% | 96.77% | 96.77% | 96.77% |

The baseline failure taxonomy was 38 correct candidates absent, two outside the
simulated global cap of eight, and ten at rank one. Its unique pre-cap recoveries
were title 0, redirect 0, alias 0, and anchor 2. Head R@16 was 62.5%, torso 6.67%,
and tail 0%; mean fit entropy was 2.018 nats and mean unresolved mass was 0.260.
There were no unseen-holdout surfaces among the aligned rows. The full 144-case
baseline completeness was only 36.81% at K=32, including five no-mention cases.

The diagnosis is constructive: the capture commonly retained `And`, `May`, or
`Boeing` while the query still contained the informative span (`1700s`, `May 28`,
or `Boeing 747`). A surface-only fuzzy control remains weak at 19.35% tuning R@32.
Enumerating generic one/two-token query spans before character retrieval fixes the
actual mention boundary and spelling errors without case-specific rules.

## Selected working architecture

`factory-precap-plus-generic-query-span-char-trigram-dice-osa`

- independent Factory title/redirect/alias/anchor candidates;
- generic query-span hypotheses;
- character trigram Dice retrieval with Damerau-OSA verification;
- development-selected threshold 0.80 and fuzzy weight 0.70;
- canonical entity-ID union across all channels before one global K=32 cap;
- authoritative redirect provenance may retain an otherwise stopword-shaped span;
- zero learned parameters, 368,369 normalized surfaces, 5,909,296 postings;
- 32,282,740 logical resident bytes.

On tuning, the model makes 24 unique recoveries and adds 77.42 recall points at
K=32 over the Factory baseline. Pre-cap pools average 31.74 candidates (p95 55),
and 19/31 queries saturate the K=32 cap. The sole miss is `eKrnel (computer
science)`: its gold entity ID does not exist in the authoritative canonical or
alias registry. Therefore 30/31 is the legal maximum on this cohort.

The exact-alignment cohort is small (19 development, 31 tuning). The remaining 187
runtime mentions are explicitly alignment-quarantined, so no gold attribution is
invented for them. Factory captured anchor proposals are also not source-split
filtered; the published exact FST remains the leakage-safe fit-only substrate.

## Decision and compression

This is decision branch **A**: candidate completeness exceeds 90%. Per the mission,
the 0.25M/1M learned semantic sweep was not run. A learned model cannot legally
recover an entity absent from the canonical registry, and broad neural/compression
experiments would add cost after the candidate and downstream gates already opened.
No new representation-compression claim is made.

Top-1 is still weak (19.35% tuning), but downstream bounded search operates over the
development-selected candidate set rather than forcing top-1. The fresh strict result shows
that this is sufficient to open the policy gate; unresolved mass is not collapsed
into a forced entity selection.

## P4 analytical projection

The selected architecture alone was projected with the unchanged
`aethercore.v11-p4-scalar-reference.v1`. These are analytical projections, not board
measurements.

| Clock | p50 virtual latency | p95 virtual latency |
|---:|---:|---:|
| 200 MHz | 116.05 ms | 236.61 ms |
| 300 MHz | 63.65 ms | 129.94 ms |
| 400 MHz | 37.45 ms | 76.67 ms |

The address index has zero learned parameters and 32.28 MB logical storage. The
per-span postings cap is reached on all aligned queries, so further work should
optimize postings layout/I/O rather than reopening semantic model families.

## Fresh strict 695-state reachability

The strict run regenerated query-span candidates from the selected 397k model for
each unique development/tuning query, added them monotonically to the original
retained replay state, applied exact typed-value repair, and reran bounded best-first
and beam search. It did not substitute baseline reaggregation.

| Metric | Result |
|---|---:|
| Certified reachable | **572/695 (82.3022%)** |
| Published v11 baseline | 324/695 (46.6187%) |
| Net increase | +248 states / +35.6835 points |
| Valid entity binding | 640/695 |
| Canonical goal present | 638/695 |
| Candidate IDs added | 12,028 |
| Address-capacity exhaustion | 0 |

Residuals are 55 `SEMANTIC_ADDRESS_GENERATION`, 29 `VALUE_AVAILABILITY`, and 39
`STATE_REPRESENTATION_OR_TOOLSET`. The policy gate is unequivocally open. The
shortest next step is the smallest controller policy over the 572 valid states;
there is no justification for another broad semantic-address or compression sweep.

## Reproduction

```bash
PYTHONPATH=src:. python scripts/droid/v12_real_corpus_qualify.py \
  --factory /external/v12-factory \
  --output /tmp/v12-real-corpus-qualification.json

PYTHONPATH=src:.:scripts/droid python \
  scripts/droid/v12_real_corpus_reachability.py \
  --bundle /external/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --mission5-report reports/droid/v10/mission5-real-reachability.json.gz \
  --aliases /external/v12-factory/address/397k/aliases.jsonl.gz \
  --output /tmp/v12-real-corpus-reachability.json
```

Authoritative compact metrics are in
`reports/droid/v12/semantic-address-v2-real-corpus-qualification.json`. Large Factory
payloads, indexes, replay rows, and checkpoints are deliberately excluded from Git.
