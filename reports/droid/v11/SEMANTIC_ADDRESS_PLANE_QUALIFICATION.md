# Semantic Address Plane v1 qualification

## Decision

`IMPLEMENT_GENERIC_PLANE_DEFER_CONTEXTUAL_SPECIALIST`

The supplied occurrence statistics are valid and sufficient to implement a
generic, uncertainty-preserving Semantic Address Plane. They are not sufficient
to train the deferred contextual entity specialist or to distinguish candidates
never generated from candidates removed before the retained top eight.

No evaluation or final-held label was copied, inspected, scored, or used for
feature design. No contextual model was trained. The private gzip payloads are
not included in this checkpoint.

## Verified identity and partition safety

| Artifact | SHA-256 |
|---|---|
| hard negatives gzip | `b544edbb46570d09c6efc415bd77806f24331efa655f93682ebab28c40ec33ec` |
| hard negatives JSON | `6626c50dcb4526c09a54a2fedecd466e5151a12f61f673612ecdb83c6a649f85` |
| hard negatives manifest | `f8446446798fabb99832d690e75686514f0c0e59933eca2e530f67b60652c7a6` |
| 10k occurrence statistics gzip | `51fc63821b5291a12e4537563f5d085cfd1c52d38f02c017eef2ae73a047bb6b` |
| 10k occurrence statistics JSON | `5f8a7ab198ba52cb93b6cb42f894b80276289124a4ae1bc57da7b288fd898af8` |
| occurrence statistics manifest | `1ca7b85500ac7a21d5beec3235391262e8ddce8f5c3834be94c2720d0c404520` |

The hard-negative set contains exactly 74 development cases / 153 replicas and
101 tuning cases / 193 replicas. All 346 case-tier keys are unique, all replicas
are training-eligible, all 528 mention offsets copy their query text exactly,
and the partition case-ID hashes match the manifest. Evaluation and final-held
are declared sealed and are absent.

The attached hard-negative gzip and manifest are byte-identical to the prior
Mission 6 freeze. The new entity capture is therefore the 10k occurrence
overlay, not a replacement mention-alignment or pre-cap trace.

## Occurrence evidence

| Measure | Result |
|---|---:|
| requested mention surfaces | 152 raw / 151 normalized |
| covered normalized surfaces | 126 |
| missing normalized surfaces | 25 |
| occurrence-statistic rows | 345 |
| represented anchor occurrences | 6,112 |
| ambiguous / unambiguous mentions | 76 / 50 |
| canonical / unresolved target rows | 133 / 212 |
| mentions with any canonical address | 108 / 126 |
| mean canonical probability mass | 0.703537 |
| mean ambiguity entropy | 0.431550 nats |
| median / maximum occurrence support | 1 / 1,296 |
| median / maximum distinct-source support | 1 / 496 |
| title / redirect signal rows | 85 / 8 |

Every row exactly reproduces alpha=1 Laplace smoothing from its occurrence
counts, and each mention's probabilities sum to one before unresolved target
mass is separated. Canonical IDs match the deterministic v0.5 ID derived from
the authoritative target title. The original 10k SQLite source pack is not
attached, so its SHA is authenticated by the exporter manifest but cannot be
re-read independently in Work. The 25k and 397k raw occurrence tables no longer
exist and are not synthesized from selector aliases.

## What the replay can distinguish

| Conservative retained-state class | Replicas |
|---|---:|
| mention set empty | 18 |
| required address absent from retained set | 153 |
| required address set incomplete in retained set | 100 |
| required address top-ranked but not selected | 54 |
| required address present, selection incomplete | 21 |

All 528 `correct_entity_per_mention` values remain null. Twenty-six detected
mentions already contain eight retained candidates, but the pre-cap pools and
generation ranks are absent. Consequently, the 253 absent/incomplete replicas
cannot be split honestly into `candidate_not_generated` and
`candidate_outside_cap`. Partial mention misses are also unlabelable; only the
18 completely empty mention sets are directly observable.

## Address coverage and reachability implication

The existing retained pools contain every case-level required entity for
75/346 replicas and 55/175 unique cases. Adding every canonical address in the
10k occurrence overlay raises those ceilings to 77/346 replicas and 56/175
unique cases—only two additional tuning replicas.

Against the fixed 695-replica Mission 5 protocol:

- Mission 5: 260/695 = 37.4101%;
- Mission 6: 306/695 = 44.0288%;
- if only the two newly complete address sets became valid repairs, the
  optimistic ceiling would be 308/695 = 44.3165%;
- even perfect selection for all 77 address-complete entity residual replicas
  would be only 383/695 = 55.1079%.

These are candidate-set upper bounds, not measured semantic recovery. This
targeted overlay cannot establish the greater-than-60% gate by itself.

## Implemented plane

`SemanticAddressPlane` validates and exposes:

- stable canonical entity IDs as the only authoritative addresses;
- alpha-smoothed P(entity|mention) without renormalizing unresolved mass;
- raw occurrence and distinct-source support;
- source diversity and ambiguity entropy;
- title prior, redirect prior/support, and alias-channel types;
- retained candidate rank/confidence annotations that do not alter the corpus
  probability; and
- a conservative qualification taxonomy that never invents pre-cap provenance.

The contextual 0.25M/1M/3M/5M sweep remains deferred. Honest training still
requires development-only mention-aligned positives, raw occurrence context,
and pre-cap candidate-generation provenance; tuning may then be used only for
calibration, successive halving, and model selection.
