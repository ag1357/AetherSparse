# Mission 7 Lane A/I - Semantic Address v2 data and leakage audit

Status: **implementation complete; real full-corpus compile pending one targeted
Factory/S600 handoff**.

## Reproduced starting point

The lane is based exactly on commit
`a7dcb187a985164648549eb18f67a7a6a4a964c6`. The corrected authenticated replay
at `artifacts/v12-authenticated-replay/controller-replay-3tier` passes the
repository verifier with bundle identity `099cd28b...f0246`, cases identity
`1254196c...13aa`, 6,150 cases, and 54,477 decisions.

An independent per-case reaggregation of the published v11 strict evidence
reproduces `324/695 = 46.6187%`. All 695 keys exactly equal the Mission 5
development/tuning controller-failure cohort. No evaluation/final-held row is
in the qualification:

| residual limitation | replicas |
|---|---:|
| `SEMANTIC_ADDRESS_GENERATION` | 355 |
| `EVIDENCE_RETRIEVAL` | 8 |
| `VALUE_AVAILABILITY` | 7 |
| `TOOLSET_CONTROLLER` | 1 |

This is a reproduction of authenticated certification evidence, not a fresh
search rerun: the private v11 occurrence and value payloads needed to rerun the
search are not present in Work. The distinction is explicit in
`address-data-evaluation.json`.

## Actual real-corpus data scope in Work

- The v11 aggregate records 345 10k surface-target rows, 6,112 occurrences,
  and 126 covered normalized surfaces. Its row-level gzip is not present.
- No occurrence-level 10k, 25k, or 397k corpus pack is present.
- Explicit mention alignment remains absent.
- Pre-cap candidate-generation provenance remains absent.
- The authenticated 397k candidate diagnostic is present: 2,050 rows split as
  271 development, 414 tuning, 1,019 evaluation, and 346 final-held. The audit
  extracted partition metadata only; it did not parse sealed candidate
  payloads. Its manifest explicitly says the pool is post-cap and lacks
  retrieval-channel, alias, redirect, and semantic provenance.

Therefore no lawful full-corpus mention recall, entity recall@K,
multi-entity completeness, channel union gain, or contextual-specialist
readiness claim can be made from currently available data. Absence is not
treated as architecture falsification.

## Implemented substrate

The versioned v2 compiler now provides:

- stable canonical ID/title registry;
- aliases and recursively resolved redirects, with duplicate titles, missing
  targets, and redirect cycles quarantined;
- every hyperlink occurrence with copied mention and context, exact Unicode
  codepoint offsets, source document/hash/span identity, canonical target, and
  redirect path;
- disk-backed per-surface support, deduplicated source-document diversity,
  empirical `P(E|mention)`, ambiguity entropy, and unresolved probability mass;
- independently recomputed `fit`, `fit+calibration`, and descriptive `all`
  statistics views, with the included source splits and lawful usage carried
  on every record and in the manifest;
- fit/selection readers that reject any view containing calibration or holdout
  occurrences, plus holdout readers that reject the `all` view;
- deterministic source-document fit/calibration/holdout occurrence splits and
  holdout-only surface flags emitted only in the descriptive view;
- immutable deterministic gzip JSONL streams, stable content-addressed record
  IDs, a shared self-addressed bundle identity, and full compressed/
  uncompressed hashes and row counts;
- a strict canonical ID/title registry used by a verified exact-FST adapter;
  noncanonical IDs, title mismatch, bundle mismatch, and held-out occurrence
  leakage are rejected;
- a targeted Factory exporter that unions title/redirect/alias/anchor channels
  before retention and retains pre-cap channel/global ranks, immutable source
  provenance, raw channel evidence, and a separately bounded channel score;
- physical separation of gold-free runtime features, development fitting
  labels, tuning-only scoring labels, and ambiguous alignment quarantine; and
- exact failure states: mention missing, correct candidate absent, outside cap,
  misranked, rejected by confidence, or correctly selected, whenever exact
  alignment/provenance supports the distinction.

Canonical IDs and source offsets are authoritative. Priors, probabilities,
and later approximate-channel scores remain proposals. The source pack is
opened `mode=ro&immutable=1`; the tests verify its hash is unchanged.

## Measurements and falsification status

The compiler is validated on a deterministic source-bound SQLite fixture. Two
independent compiles have identical stream hashes. Tests cover copied offsets,
unresolved mass, alias/redirect resolution, duplicate-title quarantine,
redirect cycles, manifest corruption, cap loss, development/tuning label
separation, sealed-partition rejection, and source immutability. Adversarial
coverage now also checks canonical ID/title mismatch, cross-bundle identity
mismatch, closed record schemas, stable record IDs, per-view probability and
entropy recomputation, unique-document diversity, unseen holdout surfaces, and
lossless exact title/alias/redirect/anchor/unresolved provenance.

The earlier aggregate-statistics design mixed fit, calibration, and holdout
occurrences. That interoperability audit defect is closed: no fitted or
selection-time consumer can request a statistic containing calibration or
holdout evidence. The `fit+calibration` view is available only after selection
for holdout qualification, while `all` is descriptive only.

No full-corpus bytes, latency, support distributions, or recall are reported:
doing so without the actual packs would be fabricated. The existing 10k pack
manifest describes 10,000 documents and 779,447 anchors, confirming that the
implemented streaming/disk-backed path is required rather than an in-memory
fixture-only design.

## Decision and next action

Lane decision: `FACTORY_ADDRESS_V2_CAPTURE_REQUIRED`.

Run the single exact handoff in
`docs/reproduction/V12_SEMANTIC_ADDRESS_DATA_HANDOFF.md`, then use its hashed
streams for the matched FST/fuzzy/semantic/fusion evaluations. The contextual
specialist and global AetherCore policy gates remain closed until lawful tuning
candidate completeness reaches at least 90% and strict reachability is rerun.
