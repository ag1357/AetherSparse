# Mission 7 Co-op Review Handoff

## Decision and identity

**Decision:** `ADDRESS_SUBSTRATE_INADEQUATE` for the data available in this
workspace—not `SEMANTIC_ADDRESS_V2_FALSIFIED`.

- Base: `a7dcb187a985164648549eb18f67a7a6a4a964c6`
- Qualified implementation: `d69f9aa1e73c9c290576c38ef16b6b94d17b0973`
- Next action: `FACTORY_ADDRESS_V2_CAPTURE`

Mission 7 produced working v2 infrastructure: a deterministic real-corpus
compiler contract, canonical registry validation, split-safe priors, exact FST,
fuzzy generation, complete union-before-cap records, calibrated-belief contracts,
ANN supervision/index manifests, specialist gates, direct claim addressing, and
analytical P4 accounting. Runtime activation is held because the required
occurrence-level Factory export was not present to populate and qualify that
infrastructure.

## Evidence boundary

| Source | Scope | Limitation |
|---|---:|---|
| Authenticated replay | 6,150 cases / 54,477 decisions | Baseline and integrity only |
| v11 10k aggregate | 345 rows / 6,112 occurrences / 126 surfaces | Raw rows absent |
| 397k diagnostic | 271 dev / 414 tuning | Query-conditioned post-cap titles only |
| Factory 10k/25k/397k occurrence pack | Absent | Blocks integrated recall/calibration |
| Evaluation/final-held | Metadata counts only | Candidate bodies and labels excluded |

## Primary result

Mention recall, integrated entity R@1/4/8/16/32, multi-entity completeness,
cross-channel union gain, v12 calibration, integrated P4 cost, observer counts,
and a new strict-695 rerun are `N/A`, with machine-readable reasons. They were
not backfilled from incompatible proxies.

This is close enough to continue as an engineering architecture, but not close
enough to activate as a measured model: on the available tuning title proxy,
fuzzy all-channel recovery reaches 84.97%@16 and 85.25%@32, while addressable-only
recovery reaches 93.39%@16. That supports the exact+fuzzy direction and shows the
remaining gap is dominated by missing global titles/occurrences. It does not
prove full-corpus mention-aligned recall.

## Qualified components

| Component | Result | Disposition |
|---|---|---|
| Address compiler/contracts | Closed schemas, stable IDs, bundle hashes, split-safe views | Ready for Factory capture |
| Exact FST | Deterministic 1.54 MB targeted artifact; 23.78% dev→tuning title transfer | Mechanics qualified; rebuild from canonical bundle |
| Fuzzy exact+char | 84.97%@16 tuning title proxy; 7.15 MB standalone; 4.68 ms nominal p95 analytical | Retain implementation; inactive pending real bundle |
| Edit distance | 2 unique tuning recoveries; pushes standalone layout to 14.77 MB | Keep optional/external, not resident default |
| SimHash/LSH | 0 unique tuning recoveries; +0.80 MB | Do not retain |
| Static ANN compression | Int8 preserves 98.24% of untrained float top-16 | Mechanics only; semantic training not run |
| Fusion/calibration | Full provenance, unresolved state, hash-bound readiness | Ready; no lawful fitted examples |
| Context specialist | Gate and sweep hook implemented | Blocked until ≥90% candidate completeness |
| Direct claim address | 25.32% R@16 vs 90.22% repaired retained pool | Keep infrastructure inactive; retain fallback |

The fuzzy proxy is close to the 90% readiness region only after normalizing by
entities already present in its limited title universe. The correct pivot is to
populate the shared address substrate, not to add more ranking complexity.

## Cost boundary

The selected fuzzy exact+char title path analytically projects to p95
7.97/4.68/3.02 ms at 200/300/400 MHz under an ideal resident-PSRAM layout.
Physical external I/O is unmeasured. Downstream direct-claim lookup projects to
p95 29.46/15.05/7.53 ms and therefore misses the nominal 10 ms target. Component
bytes cannot be summed because their measured artifacts duplicate data and use
different ID scopes.

## Strict baseline and activation

Authenticated reaggregation reproduces 324/695 = 46.6187%, with 355 address,
8 evidence, 7 value, and 1 controller residual failures. This is not a fresh v12
rerun. The address plane was not populated/repaired, so the specialist, strict
rerun, and >60% AetherCore policy gate remain closed. Existing v11 active modules
remain unchanged; all new v12 runtime modules are registered inactive.

## Single next build step

Execute `docs/reproduction/V12_SEMANTIC_ADDRESS_DATA_HANDOFF.md` once in Factory
and return the hashed canonical registry, aliases, redirects, copied-span
hyperlink occurrences, split-safe surface statistics, unresolved mass, complete
pre-cap provenance, and lawful dev/tuning alignment labels. Then:

1. compile exact and fuzzy from the same registry;
2. run the already-implemented complete union at K=1/4/8/16/32;
3. retain exact+char as the initial resident candidate and test edit as an
   external/conditional channel;
4. train semantic/specialist components only if the measured gate authorizes it;
5. rerun strict 695 only after the candidate state is actually repaired.

## Review asks

1. Is the data/truth boundary sound and appropriately conservative?
2. Are the shared canonical registry, split-safe priors, and union-before-cap
   semantics sufficient for the Factory capture?
3. Does the evidence support continuing exact+char as the working near-threshold
   path while deferring ANN/specialist training?

Detailed evidence: `SEMANTIC_ADDRESS_V2_QUALIFICATION.md` and
`semantic-address-v2-qualification.json` in this directory.
