# AetherSparse Mission 7 — Semantic Address v2 Qualification

**Decision:** `ADDRESS_SUBSTRATE_INADEQUATE`

**Base commit:** `a7dcb187a985164648549eb18f67a7a6a4a964c6`  
**Final qualified implementation commit:** `d69f9aa1e73c9c290576c38ef16b6b94d17b0973`

The final implementation SHA is the code-and-lane-artifact boundary immediately
before this deterministic aggregate/report archive. The published branch-tip SHA
is recorded in the release handoff because a commit cannot contain its own hash.

The decision applies to the evidence available in this workspace, not to a
fully populated Semantic Address v2 architecture. The contracts and mechanisms
are implemented, but the occurrence-level Factory/S600 address export required
to measure the primary experiment is absent.

## 1. Actual real-corpus data scope

| Evidence | Available scope | Permitted use |
|---|---:|---|
| Authenticated three-tier replay | 6,150 cases; 54,477 decisions | Integrity checks and independent reaggregation |
| Mission 5 development+tuning cohort | 695 cases | Baseline evidence reaggregation only |
| v11 10k address aggregate | 345 rows; 6,112 occurrences; 126 surfaces | Historical aggregate reference; row payload absent |
| 397k candidate diagnostic | 271 development + 414 tuning cases | Post-cap, query-conditioned title proxies |
| Evaluation/final-held diagnostic rows | 1,019 + 346 metadata rows | Counts only; candidate bodies not parsed |
| Occurrence-level 10k/25k/397k Factory export | **Absent** | Blocks primary qualification |

The 397k diagnostic does not contain aliases, redirects, hyperlink occurrences,
pre-cap candidates, channel provenance, mention alignment, or a semantic sidecar.
It therefore cannot support a global corpus-address recall claim.

## 2. Primary Mission 7 result

The primary scope is one canonical-ID, mention-aligned, full-corpus union in which
every channel emits complete proposals before one global cap.

| Required result | Value | Status/reason |
|---|---:|---|
| Mention-hypothesis recall | N/A | No full-corpus occurrence rows or mention alignment |
| Entity recall@1 | N/A | No matched exact/fuzzy/semantic union |
| Entity recall@4 | N/A | Same |
| Entity recall@8 | N/A | Same |
| Entity recall@16 | N/A | Same |
| Entity recall@32 | N/A | Same |
| Multi-entity completeness@1/4/8/16/32 | N/A | No mention-aligned required sets and channel outputs |
| Per-channel unique recovery / union gain | N/A | No matched cross-channel experiment |
| Top-1 resolution after high recall | Not run | Candidate-generation gate closed |
| Head/torso/tail and unseen-surface slices | N/A | Compiler supports the views; no real pack was compiled |
| v12 NLL/Brier/ECE/risk-coverage | N/A | No lawful fitted address distribution |
| Integrated address-plane P4 cost | N/A | No selected shared physical layout/cost trace |
| New strict 695-state reachability | Not run | Address state was not repaired |

Leaving these values unavailable is the qualification result. Substituting a
title-table, compression-fidelity, or downstream-claim metric would cross the
truth boundary.

## 3. Scoped proxy results — not primary/full-corpus metrics

### Exact FST title-table proxy

The immutable UTF-8 FST and lossless integer priors were measured on 4,867
development title surfaces / 4,913 diagnostic document IDs. Those IDs are not
the populated v2 canonical registry, so this result qualifies serialization and
title-table mechanics only.

| Proxy | R@1 | R@4 | R@8 | R@16 | R@32 |
|---|---:|---:|---:|---:|---:|
| Development self-roundtrip | 99.0637% | 100% | 100% | 100% | 100% |
| Development→tuning title transfer | 23.6203% | 23.7822% | 23.7822% | 23.7822% | 23.7822% |

### Fuzzy title-substrate proxy

The corrected fuzzy implementation retains complete per-generator proposals,
unions them before one global cap, preserves full retained/pruned records, and
propagates numeric unresolved and omitted mass. Qualification still uses the
post-cap query-conditioned title universe and case-level required IDs.

| Tuning proxy | @1 | @4 | @8 | @16 | @32 |
|---|---:|---:|---:|---:|---:|
| All-channel entity-ID recovery | 46.4481% | 80.6011% | 83.3333% | 84.9727% | 85.2459% |
| Required-set completeness | 44.0397% | 76.4901% | 79.8013% | 81.7881% | 82.1192% |

At K=32, character n-grams uniquely recover 9 tuning required IDs and edit
distance uniquely recovers 2. The union adds 26 required IDs and 26 complete
cases over fuzzy-normalized exact matching. SimHash/LSH has zero unique tuning
recoveries and is not retained. The selected resident proxy is fuzzy-normalized
exact + character n-grams; it is not the ExactAddressIndex FST.

### Legacy availability proxy

The v11 tuning aggregate has 37/193 complete cases with retained K≤8 and 39/193
with the 10k overlay at K≤16 (19.1710% and 20.2073%). This diagnostic cannot
authorize v12 training or specialist activation.

## 4. FST/posting and semantic-index footprints

| Component measurement | Bytes | Boundary |
|---|---:|---|
| Targeted exact FST total | 1,542,711 | 4,867 development title surfaces |
| Exact core excluding provenance | 967,765 | Dictionary + postings/entity table |
| Exact dictionary | 487,420 | Path-compressed UTF-8 FST |
| Exact postings/entity table | 478,647 | Lossless support ratios |
| Exact provenance | 574,946 | Separate exact provenance |
| Selected fuzzy exact+char standalone | 7,151,500 | Query-conditioned title proxy |
| Fuzzy exact+char+edit standalone | 14,765,797 | Misses 8 MiB resident target |
| Fuzzy all-channel standalone | 15,568,004 | Includes rejected SimHash |
| Static float32 semantic proxy | 5,030,912 | 4,913 development titles |
| Static BQ64/BQ128/BQ256 codes | 39,304 / 78,608 / 157,216 | Code bytes only |
| Partial PQ8/PQ16 codes | 39,304 / 78,608 | Plus 16,384-byte codebook |
| Static int8 index | 1,277,380 | Compression-fidelity proxy |

These measurements are not additive. They duplicate surface/entity data, use
different ID universes and scopes, and were not serialized as one shared layout.
The integrated subsystem footprint and ≤8 MiB target therefore remain unassessed.

## 5. BQ/PQ/FWHT/int8 ablations

All values below are mean top-16 overlap with an untrained, parameter-free
256-dimensional static float title reference. They are compression fidelity,
not entity correctness, semantic accuracy, or address recall.

| Encoding | 64 | 128 | 256 / full |
|---|---:|---:|---:|
| Raw sign BQ | 5.47% | 8.98% | 13.38% |
| Prefix-block FWHT sign | 8.11% | 17.19% | 32.23% |
| Global FWHT sign | N/A | N/A | 30.96% |
| PQ ADC | 14.84% (8-byte) | 17.97% (16-byte) | N/A |
| Int8 full-vector rerank | N/A | N/A | 98.24% |

PQ used 16 centroids per subquantizer (four effective bits); full 256-centroid PQ
was not qualified. Global FWHT was a single-seed diagnostic and was not selected;
no universal claim is made against a future learned dense code. Learned rotation
and the 0.25M/1M/3M/5M encoder sweep were not run because lawful hyperlink
supervision was unavailable.

## 6. Canonical union, calibration, and specialist

The v12 contracts now provide:

- a manifest-bound `AddressBundleIdentity` and strict ID↔title registry;
- closed, versioned record and manifest schemas with stable record IDs;
- split-safe `fit`, `fit+calibration`, and descriptive `all` statistics views;
- a verified compiler→exact adapter and compiler→ANN supervision adapter;
- complete pre-cap counts, caps, ranks, source records, and full pruned sidecars;
- raw score and separately bounded channel score semantics;
- numeric unresolved mass and calibrated availability-vs-resolved metric scopes;
- content-addressed fusion/belief envelopes;
- specialist authorization only from hash-matched qualification and source,
  alignment, and complete-pre-cap manifests.

No caller-provided floats or booleans can open the specialist gate. Current
lawful development and tuning example counts are both zero, so calibration was
not fitted and successive halving did not start.

The v11 weak case-level relevance reference—NLL 0.460704, Brier 0.145811,
ECE10 0.118863—is retained only as a warning baseline. It is not
P(entity|mention, context) and is not reported as v12 calibration.

## 7. P4 projection boundary

The unchanged calibration is `aethercore.v11-p4-scalar-reference.v1`. All
numbers are analytical projections, not hardware measurements.

| Scoped path | 200 MHz p50 / p95 | 300 MHz p50 / p95 | 400 MHz p50 / p95 |
|---|---:|---:|---:|
| Selected fuzzy exact+char title proxy | 6.9583 / 7.9680 ms | 4.0220 / 4.6778 ms | 2.5571 / 3.0166 ms |
| Downstream direct-claim lookup | 1.1299 / 29.4626 ms | 0.5756 / 15.0451 ms | 0.2884 / 7.5263 ms |
| Integrated address subsystem | N/A | N/A | N/A |

The fuzzy projection assumes an ideal resident-PSRAM title index and does not
project physical external I/O. The direct-claim projection separately accounts
resident postings and external exact-source regions with page-aligned transfers;
its nominal p95 exceeds 10 ms. Neither row is an integrated address-plane cost.
Exact FST and ANN runtime costs remain unqualified.

## 8. Addressed claim/evidence path

On the unchanged 695-case post-retrieval replay cohort:

| Claim selection path | R@16 |
|---|---:|
| Repaired v11 claim pool over retained FTS/BM25-selected evidence | 90.2158% |
| Direct entity→relation→type address | 25.3237% |
| Direct, then unresolved FTS/BM25 fallback | 77.2662% |

The replay evidence is oracle-contaminated and this is not raw FTS/BM25 or a
fresh pack retrieval experiment. Direct addressing is structurally qualified
but recall-dominated, nominally too slow at p95 under the reference assumptions,
and inactive in the v12 registry.

## 9. Strict baseline and gate

Independent reaggregation of the authenticated replay reproduces 324/695 =
46.6187% reachable, with residual:

- 355 `SEMANTIC_ADDRESS_GENERATION`
- 8 `EVIDENCE_RETRIEVAL`
- 7 `VALUE_AVAILABILITY`
- 1 `TOOLSET_CONTROLLER`

This is baseline evidence reaggregation, not a new v12 search rerun. Because no
integrated v12 candidate state repaired the address plane, the required strict
rerun was not authorized. The >60% AetherCore policy gate remains closed.

## 10. Exact remaining bottleneck and next action

The bottleneck is one missing artifact: a content-addressed occurrence-level
Factory/S600 Semantic Address v2 export with canonical registry, aliases,
redirects, copied-span hyperlink occurrences, split-safe statistics, unresolved
mass, complete pre-cap channel provenance, and lawful development/tuning labels.

The next justified action is exactly `FACTORY_ADDRESS_V2_CAPTURE`, using
`docs/reproduction/V12_SEMANTIC_ADDRESS_DATA_HANDOFF.md`. After that single
capture:

1. compile exact and fuzzy indexes from the shared canonical registry;
2. generate complete channel outputs and union once before K;
3. measure matched recall/completeness at K=1/4/8/16/32;
4. fit/calibrate only through the declared split-safe views;
5. measure a shared physical layout and P4 200/300/400 costs;
6. run the specialist and strict 695 rerun only if their gates open.

## 11. Architecture decision

The v11 active architecture remains unchanged. Every new v12 runtime module is
registered but inactive in
`config/architecture/aethercore-v12-semantic-address-v2.registry.json`.
The compiler/contracts are ready for the Factory capture; exact FST, fuzzy,
semantic ANN, fusion, specialist, and direct-claim paths do not activate from
the current proxy evidence.

Machine-readable detail is in
`reports/droid/v12/semantic-address-v2-qualification.json` and its manifest.

## 12. Reproduction

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src:. .venv/bin/pytest -q
PYTHONPATH=src:. .venv/bin/python scripts/droid/v12_architecture_registry.py \
  --repository . \
  --output config/architecture/aethercore-v12-semantic-address-v2.registry.json
PYTHONPATH=src:. .venv/bin/python - <<'PY'
from pathlib import Path
from aethersparse.observer.registry import load_registry
load_registry(Path("config/architecture/aethercore-v12-semantic-address-v2.registry.json"))
PY
sha256sum -c reports/droid/v12/semantic-address-v2-qualification.manifest.sha256
```

Lane-specific reproduction commands and input identities are retained in the
v12 reproduction documents and machine-readable lane reports.
