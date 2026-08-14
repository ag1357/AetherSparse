# Mission 6 targeted upstream handoff audit

Status: **accepted with entity-scope limitations**.  The value capture is complete for
the requested residual.  The entity capture is authentic and useful, but it is not the
three-tier mention-aligned/pre-cap capture requested by
`docs/reproduction/V11_TARGETED_DATA_HANDOFF.md`.

## Integrity and split policy

The consumer audit ran `scripts/droid/v11_targeted_handoff_audit.py` against the
attached directory and a fresh extraction of the authenticated replay export.

- All eight attached files were hashed.  Each payload listed by the completion report
  matches its expected SHA-256 prefix and suffix; each gzip payload also matches the
  exact full compressed and uncompressed digest in its manifest.
- The replay tar SHA-256 is
  `572c4e3c4d210e058d9384571618e7fa4abcea7c91b9775e47f7451847ebc1ad`.
  The repository's strict verifier accepts all 6,150 cases and returns logical bundle
  identity `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`.
- The benchmark and Mission 5 report match their frozen identities
  `1e8b8942…d113` and `280b314b…27cd`.
- Entity rows are exactly 153 development plus 193 tuning replicas.  Value rows are
  exactly 16 development plus 27 tuning replicas.  No evaluation/final-held row is
  present, no entity replica is ineligible, and case IDs do not cross partitions.
- Evaluation/final-held labels remain prohibited for fitting, calibration, feature
  design, architecture selection, or threshold choice.

The machine-readable result is `reports/droid/v11/targeted-handoff-audit.json`.

## Entity field coverage

The surviving raw 10k corpus supplies 345 mention-target statistic rows over 126 of
152 requested raw surfaces (126 of 151 normalized surfaces), totaling 6,112 anchor
occurrences.  These rows provide empirical mention probability, occurrence support,
source-document diversity, ambiguity entropy, and anchor/title/redirect indicators.
Canonical `as:v050:entity:*` addresses are authoritative when present.

The limitations are material:

- the export covers 10k only; the raw occurrence corpora for 25k and 397k do not exist;
- 212 of 345 aggregate rows have no canonical target entity ID and therefore cannot be
  promoted into address hypotheses;
- all 528 frozen mention records still have `correct_entity_per_mention = null`;
- only the retained post-cap candidates are present; the pre-cap pool, generation
  count, and raw alias/redirect path are absent;
- consequently 26 mentions observed at the retained cap cannot be classified as
  outside-cap versus never-generated.

The valid scope supports a deterministic/statistical Semantic Address Plane v1 and
honest uncertainty distributions.  It does not support a real three-tier contextual
entity specialist or a lawful 0.25M/1M/3M/5M successive-halving comparison.  The
specialist gate must remain closed until candidate recall and explicit mention labels
exist.

## Value field coverage

The 43-row value capture covers all requested tiers and supplies:

- 344/344 selected chunks with exact complete text and source offsets;
- 75/75 source/compiler documents;
- 2,493 ranked regions;
- 83 runtime pre-pruning matches, all exact-surface and exact-document rebound;
- runtime state before and after region selection, deduplication, and caps; and
- compiler state before and after type/page caps.

No observed failure is caused by region pruning, deduplication, the per-chunk value
cap, or exact-document rebinding.  The stale ten `BLOCKED` labels can now be resolved:
every one lacks at least one target surface from selected top-eight chunk text.

Across all 43 replicas, only 3 contain every required exact target span in selected
chunk text.  Among the 32 replicas that do not already expose every exact target atom
to the controller, 29 are selected-chunk/source-span absence and 3 are quotation
compiler/runtime extraction misses.  The other 11 already expose the exact value atoms
and require semantic comparison binding/controller assembly rather than broader value
enumeration.

## Authorized continuation

Implementation may consume only gold-independent runtime fields when constructing
addresses or regenerated controller state.  Development labels may fit generic
statistics; tuning labels may calibrate and select a frozen alternative.  Gold source
documents and accepted target atoms in the diagnostic remain diagnostic/scoring data
and must never be passed to the repair constructor.

The work therefore proceeds with a bounded 10k-backed Semantic Address Plane, generic
typed value corrections supported by selected source state, a specialist-readiness
gate, regenerated controller input, and the unchanged Mission 5 certified reachability
protocol.  Policy/fusion/depth experiments remain conditional on strict reachability
above 60%.
