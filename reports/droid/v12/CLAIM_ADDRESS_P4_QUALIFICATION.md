# Mission 7 lanes G/H: addressed claims and ESP32-P4 cost

## Decision

`DIRECT_CLAIM_ADDRESS_INFRASTRUCTURE_QUALIFIED_RETRIEVAL_NOT_QUALIFIED`

Base commit: `a7dcb187a985164648549eb18f67a7a6a4a964c6`.

The exact `canonical entity -> relation -> typed claim -> source region` path is
implemented, deterministic, bounded, integrated with the existing typed value
lattice, evidence graph, pointer-copy planner, and exact verifier. It is not
enabled as a replacement for weighted FTS/BM25. On the current malformed v11
address state, direct addressing loses recall, and its projected nominal p95 also
exceeds the latency target under the declared layout proxy. Recall outranks latency.

## Truth and data boundary

- Authenticated replay: 6,150 cases / 54,477 decisions, bundle
  `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`.
- Qualification cohort: unchanged 695 Mission 5 development/tuning failures:
  294 development and 401 tuning; 196/234/265 at 10k/25k/397k.
- Evaluation and final-held cases were not consumed.
- Constructors use only the current query frame and retained exact claims/spans.
  Accepted answers are read only after selection to compute recall/correctness.
- The replay contains evidence-oracle state. Therefore this is a post-retrieval
  selection ablation, not a fresh corpus-retrieval qualification.
- The comparator is the repaired v11 claim pool over retained weighted
  FTS/BM25-selected evidence. It is not raw FTS/BM25. Replay does not retain FTS
  postings, all candidate chunk bytes, or an ANN index.

## Recall-first matched result

| System | R@1 | R@4 | R@8 | R@16 | R@32 |
|---|---:|---:|---:|---:|---:|
| Repaired v11 claim pool over retained FTS/BM25-selected evidence | 24.60% | 66.47% | 80.43% | 90.22% | 92.66% |
| Exact entity/relation/type direct address | 2.88% | 10.36% | 20.00% | 25.32% | 31.22% |
| Direct, then FTS/BM25 on unresolved address | 15.68% | 46.47% | 64.75% | 77.27% | 83.74% |

The direct lookup is non-empty in 361/695 cases. It reports an unresolved entity
in 120 and an unresolved relation in 82; the gold-independent unresolved fallback
activates in 420. Direct lookup produces 9.93 candidates on average (2 p50, 32
p95) versus 33.95 in the retained FTS/BM25 pool (32 p50, 64 p95).

The exact blind planner/verifier passes in 266 cases, but only 23 of those chosen
answers are canonically correct when scored post hoc. This is the expected
falsification signal: an efficient exact lookup over wrong or incomplete entity /
relation addresses is not a retrieval repair and should not be promoted.

## Exact path and value-lattice correction

`ClaimAddressIndex` unions all canonical entity/relation postings before one global
cap, preserves exact source-span pointers and hashes, fails closed on missing
relations, and never rewrites an entity, relation, value, or source. Its canonical
serialization is content addressable. Compact posting bytes are accounted
separately from immutable source-region bytes. The compact byte count is the
canonical JSON pointer serialization used by this experiment, not a claimed
production binary layout.

The integration exposed one generic v11 defect: `lattice_from_evidence` constructed
a validated lattice before deduplicating multiple claims at the same typed source
address. Lawful multi-claim records therefore failed validation. The corrected
path stably sorts and deduplicates before applying the capacity; no value or
address is invented.

## Formula-derived P4 layout proxy

The model reuses the unchanged v11 scalar calibration
`aethercore.v11-p4-scalar-reference.v1`. Work runs at full speed; there are no
sleeps. Host time, formula-derived work, analytical projection, and hardware
measurement remain separate. No runtime instruction, DMA, or page counters were
available.

The explicit physical-layout proxy keeps the query-local posting sidecar resident
in PSRAM and exact selected source regions on parameterized external storage.
Posting and source payloads are charged once, to those respective channels. Each
nonempty entity/relation/type posting region or deduplicated source span starts
with one random 4 KB page; only continuation pages within that same region are
sequential. Transfer time is charged on page-aligned physical bytes, not logical
payload bytes. Source spans are deduplicated by `span_id` within a query, with no
cross-query cache credit.

| Formula-derived per query | Mean | p50 | p95 | Maximum |
|---|---:|---:|---:|---:|
| Internal SRAM/DMA peak proxy | 2,886.4 B | 4,224 B | 6,144 B | 6,144 B |
| Query-local posting bytes resident/known peak in PSRAM | 14,601.4 B | 14,005 B | 27,447.8 B | 27,730 B |
| All eligible pre-cap posting payload bytes | 5,637.6 B | 858 B | 27,007 B | 27,730 B |
| Deduplicated selected-source payload bytes | 406.5 B | 32 B | 1,401 B | 3,123 B |
| Query-key bytes processed | 45.1 B | 49 B | 102 B | 175 B |
| PSRAM page-aligned transfer bytes | 7,920.9 B | 4,096 B | 32,768 B | 53,248 B |
| External page-aligned transfer bytes | 34,683.4 B | 4,096 B | 126,976 B | 131,072 B |
| PSRAM random / sequential pages | 1.05 / 0.88 | 1 / 0 | 5 / 5 | 10 / 6 |
| External random / sequential pages | 8.47 / 0 | 1 / 0 | 31 / 0 | 32 / 0 |
| Formula-derived scalar integer operations | 1,544.7 | 474 | 6,042 | 6,813 |
| Candidates before address | 33.95 | 32 | 64 | 64 |
| Candidates after typed address, before cap | 13.14 | 2 | 64 | 64 |
| Candidates after cap | 9.93 | 2 | 32 | 32 |

The operation formula is `4 * query-key bytes + 8 * eligible pre-cap records +
12 * (n * ceil(log2(n))) + deduplicated source payload bytes`. The SRAM proxy is
one reusable 4 KB page buffer when a page is read, otherwise 256 bytes, plus 64
bytes per selected record. These constants are declared analytical proxies, not
observed instruction counts.

FST, BQ, PQ, int8, XOR/popcount, SIMD, neural MACs, active parameters, and model
bytes are all exactly zero for this lane. Those zeros mean “channel absent,” not a
claim about the parallel address/ANN lanes. Centroid bytes are likewise zero.
Serialized directory bytes are unavailable; query-key bytes are reported without
mislabeling them as a directory read. Query-local posting bytes exclude that
directory and are not a full-corpus index or full address-subsystem footprint.

## Analytical latency

| Scenario | Reference storage bandwidth / random access | Projected p50 | Projected p95 |
|---|---:|---:|---:|
| 200 MHz | 5 MB/s / 100 us | 1.1299 ms | 29.4626 ms |
| 300 MHz nominal | 10 MB/s / 60 us | 0.5756 ms | 15.0451 ms |
| 400 MHz | 20 MB/s / 30 us | 0.2884 ms | 7.5263 ms |

These are v11 analytical reference assumptions, not eMMC figures and not board
measurements. Storage bandwidth and random-access values remain parameterized.
Measured Work-host build and lookup distributions are retained in the machine
aggregate under `measured_work_host_not_p4`; they are empirical and expected to
vary between runs. No actual P4 hardware measurement exists.

## Pareto and retained architecture

Under the unchanged v11 reference assumptions, the direct point's nominal p95 is
15.0451 ms, above the 10 ms target, and its R@16 is only 25.32%. Its query-local
posting allocation is small, but the full-subsystem 8 MB PSRAM target cannot be
evaluated without the serialized directory and full-corpus index footprint. The
direct point is recall-dominated by the retained comparator. A complete latency
Pareto frontier cannot be claimed because replay omits the comparator's posting
reads and unselected chunk bytes.

Retain the exact direct claim path as an inactive downstream capability. Activate
it only after the parallel Semantic Address Plane v2 produces high-recall canonical
entity/relation unions; retain weighted FTS/BM25 or whole-passage retrieval as the
fallback. Do not use fast direct lookup to hide upstream address failure.

Offline sparse document expansion and whole-passage ANN were not run because no
lawful full pack/expansion/ANN index is present in Work. No S600/full-corpus battery,
training sweep, evaluation, or final-held run occurred.

## Reproduction

```bash
PYTHONPATH=src python scripts/droid/v12_claim_address_qualify.py \
  --bundle /path/to/authenticated/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --cohort-report reports/droid/v11/upstream-reachability.json \
  --output reports/droid/v12/claim-address-p4-qualification.json
```

Machine aggregate SHA-256:
`d37c16da9b28326b50965d58458de47149607bce3f66a47d4863bd8af79e282d`.
