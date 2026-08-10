# Mission 4 Gate Decisions and Standing Rules (user directives, 2026-08-10)

## Lane C (Phase 7, misspelling): SHORT-ACCEPTED +4.00 pp

Ship the bounded edit-distance ≤2 sidecar as landed
(src/aethersparse/selection/spelling.py, Damerau-OSA verified).  Do not
iterate: the next step needs feature support for probe-rescued documents,
measured at −12 to −38 pp in v1–v4 (the wrong-correction cliff: frequency-
or pool-supported wrong corrections swing token-overlap features toward
wrong documents).  Recorded as decisive negative: raw-surface trigram
dual-normalization recovers 0/36 displaced cases.  Tier results: +4.00 pp
misspelling strict top-1 at 10k (54→58), 25k (45→49), 100k (37→41); zero
lost cases and zero category regressions at every tier; strict recall
+0.32/+0.47/+0.77 pp.

## Lane D (Phase 8, carry): ship compat carry as-is

Fresh paired 397k measurement (same pack, same config, parallel harness):
none 68.75% vs compat 68.59% strict; carry deltas pronoun +2.00 pp,
follow_up +4.00 pp (8 gained / 2 lost).  The Mission 3 "@397k follow_up
−2 pp" regression was an artifact of a stale cross-mission baseline
(67.42%), not a real effect.  **Standing rule: every A/B claim requires a
freshly-measured paired baseline on the same pack and config.**

## Ladder marginals re-recorded on canonical terms (user correction)

Retrieval stages and controller were first compared on mixed metrics; on
canonical value accuracy alone:

| stage marginal | @10k | @25k |
|---|---|---|
| candidate generation | +1.41 pp | +1.48 pp |
| ranking | +1.25 pp | +1.57 pp |
| evidence construction | +16.56 pp | +7.89 pp |
| retrieval stack total | 19.22 pp | 10.94 pp |
| controller residual | 47.11 pp | 55.23 pp |

Retrieval's canonical worth falls 19.22 → 10.94 pp from 10k to 25k while
the controller residual rises 47.11 → 55.23 pp.  Candidate generation is
worth ~1.5 pp of product accuracy.

## Phase 5 / Phase 6: deprioritized

Justified on candidate-generation grounds, which the canonical marginals
show is ~1.5 pp of product.  The alias @100k diagnostic is complete
(reports/droid/v09/v09-alias-100k-v2.json): absent-from-pool collapses
10% → 38.6% from 25k to 100k; multi-pageid anchors 30 → 57;
disambiguation pools 13 → 19.  This is a real scale defect — deferred,
not dismissed.

## Standing metric: mode-2 → mode-3 transfer rate

Every controller change reports its mode-2 → mode-3 transfer rate.  Below
20% is a stop-and-reassess signal, not a footnote.  (Phase 3 measured 3%.)

## Phase 4 branch check (2026-08-10): evidence present → build

Of 238 COMPOSITION_OPERATOR_MISSING cases @10k, mode-3 retrieval delivers
ALL gold documents in the selected evidence for 183 (76.9%): two_source
73/89 (82.0%), three_to_six_source 39/58 (67.2%), comparison 71/91
(78.0%).  Candidate-pool presence: 236/238 (99.2%).  ≥60% threshold met →
build Phase 4 as specified; composition operators operate on cases where
evidence is already delivered, so transfer is not retrieval-blocked.

## Integrity after the exFAT incident (2026-08-10)

- s600 pack copies are authoritative; sha256: 10k aef284ff0f157d2d…,
  25k 04ea224214b540a4…, 100k 2c073191354d4d36…, full-397k
  4f232260041f4c81… (matches Mission 3 phase7-full recorded pack_sha256).
- Restored local 10k pack == s600 copy (aef284ff…).
- Benchmark JSON sha256 1e8b89427898df3c3e5e… identical in the local repo
  and the s600 checkout (also git-tracked).

## Schema reservations (landed as schema, not capability)

- Entity ID bands: corpus compiles mint `as:v050:entity:{sha256[:24]}`;
  the reserved high band `as:user:entity:{sha256[:24]}` is for user-defined
  entities (persistent conversational memory, later mission).  One binder
  ID space; no cross-minting.
- `ExactSourceSpan.source_class: CORPUS | CONVERSATION` (default CORPUS).
  Corpus = document+revision+span; conversation = conversation+turn+span
  in the same fields.
- `StructuredClaim.grounding: CORPUS_GROUNDED | USER_ASSERTED` (default
  CORPUS_GROUNDED).  USER_ASSERTED claims are ineligible to satisfy factual
  verification paths.
- Contract tests: tests/controller/test_models.py::TestSchemaReservations.
