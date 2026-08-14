# Mission 6 specialist-readiness gate

This qualification is a hard stop before the contextual entity sweep. It
verifies the targeted handoff, measures whether a bounded contextual scorer can
receive the correct entity address, and records whether the 0.25M/1M/3M/5M
successive-halving ladder may lawfully begin. It does not train a model or
change the fusion/depth architecture.

## Split contract

- Development is the only fit partition.
- Tuning is calibration and model selection only.
- `case_id` is the grouping key; tier replicas never cross partitions and are
  never treated as independent cases for fitting.
- Evaluation and final-held labels are not read.
- Candidate generation, feature definitions, and deterministic corrections
  must be frozen without inspecting tuning correctness. Tuning may select among
  those frozen alternatives.

The contextual gate requires all of the following:

1. every supplied payload and source identity verifies;
2. explicit mention-to-canonical-entity alignment for every supervised mention;
3. pre-cap candidates, generation channels, and cap/rejection provenance;
4. occurrence statistics for 10k, 25k, and 397k;
5. at least 90% candidate-complete recall on tuning overall and within every
   tier after deterministic generation is frozen.

The 90% threshold is a readiness guard, not a product accuracy target. A
candidate scorer cannot recover an address absent from its bounded support.

## Measured decision

`BLOCK_CONTEXTUAL_ENTITY_SUCCESSIVE_HALVING`

The attachment hashes and protected-partition restrictions pass, but the four
entity prerequisites do not:

| Gate | Measured result |
|---|---:|
| Explicit mention alignment | 0/528 mention records |
| Pre-cap candidate state | absent |
| Anchor tier coverage | 10k only; 25k/397k absent |
| Tuning candidate-complete recall after anchor union | 39/193 (20.2073%) |

The 10k export contains 345 mention-target statistics for 126/152 requested
surfaces, but only 133 rows have a resolvable canonical entity ID. Adding those
exact IDs to the retained candidate support changes candidate-complete coverage
from 75 to 77 of 346 replicas and from 55 to 56 of 175 unique cases. Both added
replicas are tuning-side observations; no model is fit from them.

Strictly exceeding 60% reachability requires at least 418/695 certified cases.
The current result is 306/695. Even if every one of the 77 candidate-complete
entity replicas recovered, the loose entity-only ceiling would be 383/695.
Reaching 418 would then still require at least 35 of the 43 remaining value
replicas. These are availability ceilings, not promised recoveries.

The value capture is complete for its requested boundary fields: 43/43 pack
captures, 344/344 selected chunks, no missing compiler document, and no exact
document-rebinding failure. The handoff's inherited labels (10 blocked, 17
compiler extraction, five runtime extraction, and 11 correct-value binding)
are retained only as `historical_classification_counts`; they are not presented
as a fully refined causal decomposition.

Recomputing every row from `pack_capture` source availability before inspecting
downstream stage losses yields 29 selected source/chunk absences, three dual
compiler/runtime quotation-extraction misses, and 11 semantic subject/relation
binding failures. Region pruning, deduplication, cap, and rebinding each account
for zero rows. Exact `runtime_candidate_values` are treated as existing
source-bound hypotheses; a quotation copied verbatim from a selected chunk is
treated as an available source span even when both extractors miss it.

## Reproduction

The row-level gzip inputs remain external to Git. Run from the repository root:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v11-readiness-uv uv run python \
  scripts/droid/v11_specialist_readiness.py \
  --entity-hard-negatives /path/to/ENTITY_HARD_NEGATIVES_V11.json.gz \
  --entity-manifest /path/to/ENTITY_HARD_NEGATIVES_V11.manifest.json \
  --anchor-statistics /path/to/entity-anchor-statistics-10k.json.gz \
  --anchor-manifest /path/to/entity-anchor-statistics-10k.json.gz.manifest.json \
  --value-diagnostic /path/to/value-enumeration-diagnostic-v11.json.gz \
  --value-manifest /path/to/value-enumeration-diagnostic-v11.manifest.json \
  --mission5-report reports/droid/v10/mission5-real-reachability.json.gz \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --reachability reports/droid/v11/reachability-rerun.json \
  --anchor-tier 10k \
  --output reports/droid/v11/specialist-readiness.json \
  --manifest-output reports/droid/v11/specialist-readiness.manifest.json
```

Rerunning the command must reproduce the report byte-for-byte. The manifest
contains the report SHA-256 and every input identity used by the gate.

## Unblock protocol

Once all gates pass, train every requested capacity on development only with
case-group weighting. Use tuning only to promote configurations, calibrate
confidence, and select abstention thresholds. Freeze the model, fusion, gate,
and registry identities before any held-out use. Regenerated entity states must
keep candidate support separate from the selected semantic binding, and the
affected reachability cohort must be rerun rather than carried forward.
