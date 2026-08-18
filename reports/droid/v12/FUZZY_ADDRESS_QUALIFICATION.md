# Mission 7 bounded fuzzy-address qualification

Status: **implemented and bounded-title-surface qualified**. This is not a
full-corpus address-recall claim.

## Evidence boundary

- Base commit: `a7dcb187a985164648549eb18f67a7a6a4a964c6`.
- Authenticated replay: `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`
  (6,150 cases / 54,477 decisions verified).
- Candidate diagnostic: `8dfb6c9a723a66d9dfd7d24a102a719a87b590a457fc1bab505cced771d57158`.
- Only development/tuning 397k rows entered the index or query evaluation.
  Evaluation/final-held rows were rejected before candidates or query frames
  were consumed.
- The diagnostic is post-cap and has no aliases, redirects, channel provenance,
  semantic proposals, pre-cap candidates, or full pack. Results therefore bound
  fuzzy recovery over the available title-surface universe only.

## Qualified compact baselines

Development-only deterministic real-title perturbations selected char threshold
`0.46` and SimHash maximum Hamming distance
`16`. Edit expansion is token-level
Damerau-OSA <=2 over a hashed symmetric-delete vocabulary; every proposal is
verified before its exact canonical ID is returned.

The selected runtime for cost projection is **fuzzy-normalized exact + character
n-gram**. “Exact” here means equality after the fuzzy title normalizer; it is
not the `ExactAddressIndex` FST. Edit distance remains an offline ablation;
SimHash/LSH is rejected and semantic ANN is inactive.

## Natural query results

| partition | required IDs | title-addressable | mention recall | entity recall@16 | addressable recall@16 | completeness@16 |
|---|---:|---:|---:|---:|---:|---:|
| development | 249 | 217 | 0.8474 | 0.8434 | 0.9677 | 0.7990 |
| tuning | 366 | 333 | 0.8525 | 0.8497 | 0.9339 | 0.8179 |

Per-channel recall@1/4/8/16/32, unique recoveries, union gains, bytes, logical
operations, and cap saturation are in `fuzzy-address-qualification.json`.
The addressable-normalized metric is a bounded mechanism diagnostic only; the
all-required denominator is the architecture-relevant result.

## Footprint and determinism

- Surfaces: 9972; canonical address rows:
  10000.
- Compiled JSON: 15568259 bytes; deterministic gzip:
  5553478 bytes.
- N-gram postings: 946545 bytes; edit delete postings:
  7253398 bytes; token->surface postings:
  264646 bytes; SimHash/LSH:
  802207 bytes.
- The standalone fuzzy-normalized exact+char serialized tables are
  6.82 MiB before allocator/runtime overhead. This is neither
  an additive integration estimate nor a shared-footprint deduction against
  the exact FST.
- The external compiled index round-trips byte-identically and its committed
  manifest records both compressed and decoded SHA-256 identities.
- The all-channel diagnostic serialization is 14.85 MiB decoded.
  These standalone JSON table counts do not establish integrated <=8 MiB
  residency; allocator, shared registry, and physical layout are unmeasured.

## Selected fuzzy-exact+char P4 analytical accounting

The accounting covers all 685 authenticated development/tuning
397k cases after threshold selection. It projects only the selected
fuzzy-normalized exact+char path: edit, SimHash/LSH, and semantic ANN are not
active.

- Pre-global-cap candidates: p50
  3.0, p95
  26.0.
- Logical bytes touched: p50
  67028.0, p95
  68048.0; integer operations: p50
  654190.0, p95
  808153.0.
- Posting entries read: p50
  16384.0, p95
  16384.0; XOR/popcount operations are
  zero because SimHash is inactive.
- Ideal packed 4 KiB page lower bound: p50
  17.0, p95
  17.0; logical random index reads:
  p50 162.0, p95
  290.0.
- Resident standalone selected tables: 7151500
  bytes. Packed analytical working memory: p50
  29442.0, p95
  38202.6 bytes.
- The char posting-work cap saturated in
  515 /
  685 cases
  (0.7518); the global
  K=64 cap saturated in 0.

| v11 scenario | clock | p50 virtual ms | p95 virtual ms | p50 random-access ms | p95 random-access ms |
|---|---:|---:|---:|---:|---:|
| conservative_200mhz | 200 MHz | 6.9583 | 7.9680 | 0.3240 | 0.5800 |
| nominal_300mhz | 300 MHz | 4.0220 | 4.6778 | 0.1620 | 0.2900 |
| optimistic_plausible_400mhz | 400 MHz | 2.5571 | 3.0166 | 0.0810 | 0.1450 |

These are analytical projections, not hardware measurements. They assume an
ideal resident-PSRAM layout and reuse the unchanged v11 200/300/400 MHz scalar,
bandwidth, and random-access calibration. The 4 KiB pages are ideal packing
lower bounds; random/sequential counts are logical, not observed physical page
order. No external-storage bytes or pages are assigned, so eMMC/storage latency
is deliberately unprojected. The explicit conditional formula is
`bytes/(bandwidth_MBps*1e6)*1000 + random_pages*random_access_us/1000`.

## Decision and limitation

The channel implementation is reusable and canonical-ID safe, and it measures
mention recovery separately from post-union entity recall. Its full-corpus
architecture gate remains **blocked by missing global address data**, not by
this implementation: a query-conditioned post-cap title set cannot establish
full-corpus recall, alias/redirect recovery, or never-generated versus
pre-cap-pruned failures.

The compact matched ablation rejects SimHash/LSH for this substrate: the
all-channel union and exact+char+edit both reach tuning recall@16
`0.8497`, while SimHash has zero unique tuning
recoveries and adds 802207 standalone diagnostic bytes.
Edit expansion remains an inactive offline ablation. Selected fuzzy-normalized
exact+char reaches `0.8470` at a mean
57183 logical
bytes/query. This lane does not treat that table count as an additive or shared
footprint relative to the exact FST.

## Reproduction

```bash
PYTHONPATH=src python scripts/droid/v12_fuzzy_address_qualify.py \
  --diagnostic /path/candidate-diagnostic-397k.jsonl.gz \
  --diagnostic-manifest /path/candidate-diagnostic-397k.manifest.json \
  --replay-bundle /path/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output reports/droid/v12/fuzzy-address-qualification.json \
  --report reports/droid/v12/FUZZY_ADDRESS_QUALIFICATION.md \
  --index-output /external/fuzzy-address-397k-postcap.json.gz \
  --index-manifest reports/droid/v12/fuzzy-address-index.manifest.json
```
