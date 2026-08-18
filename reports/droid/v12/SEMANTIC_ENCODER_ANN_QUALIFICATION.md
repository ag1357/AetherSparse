# Mission 7 semantic encoder and quantized ANN qualification

Status: `STATIC_MECHANICS_QUALIFIED_LEARNED_TRAINING_BLOCKED`

Decision: **do not train or retain a semantic ANN from the available Work data.**
No verified v2 compiler occurrence bundle was supplied to this measurement, so
the required natural hyperlink occurrence rows and exact mention-to-target
labels were not available to the learned lane. The 397k candidate diagnostic is
post-cap and has no semantic, alias, redirect, or pre-cap provenance. It is
suitable for index/compression mechanics, not semantic-address supervision.

## Truth and split boundary

- Integration base: `3f74d44f11e6d913520e8d3f110ce3d8912f1f0d`.
- Input diagnostic SHA-256:
  `8dfb6c9a723a66d9dfd7d24a102a719a87b590a457fc1bab505cced771d57158`.
- Development supplied the index/codebook rows: 271 cases, 20,790 candidate
  rows, 4,913 unique document IDs/titles.
- Tuning supplied queries only: 64 fixed lexicographically selected questions
  from 414 available cases.
- Evaluation/final-held rows excluded: 1,365.
- Case IDs are unique within and across partitions, and every development/tuning
  diagnostic partition matches the benchmark partition.
- Semantic correctness labels used: zero.
- Learned parameters, learned rotation, contextual successive halving: zero/not run.

Development/tuning above are benchmark partitions used only for the label-free
static proxy. They are not corpus source splits and cannot authorize learned
training.

The measured reference is a parameter-free 256D signed word/character n-gram
hash. All top-16 figures below are overlap with its exact float ranking. They
measure compression fidelity, **not** entity correctness, hyperlink-supervised
recall, or product retrieval quality.

## Matched compression screen

| Representation | Bytes/address | Index bytes | Mean top-16 overlap |
|---|---:|---:|---:|
| raw sign BQ, 64b | 8 | 39,304 | 5.47% |
| raw sign BQ, 128b | 16 | 78,608 | 8.98% |
| raw sign BQ, 256b / Hamming | 32 | 157,216 | 13.38% |
| global randomized FWHT + sign, 256b | 32 | 157,216 | 30.96% |
| prefix/block FWHT + sign, 64b | 8 | 39,304 | 8.11% |
| prefix/block FWHT + sign, 128b | 16 | 78,608 | 17.19% |
| prefix/block FWHT + sign, 256b | 32 | 157,216 | 32.23% |
| PQ-ADC, 8-byte / 16 centroids | 8 | 39,304 + 16,384 codebook | 14.84% |
| PQ-ADC, 16-byte / 16 centroids | 16 | 78,608 + 16,384 codebook | 17.97% |
| full-vector int8 rerank | 260 | 1,277,380 | 98.24% |

The PQ experiment is a **partial 16-centroid small-data screen**. Each
subquantizer therefore carries four effective bits even though the reference
stores one byte per subquantizer; it does not claim a full 256-centroid PQ
qualification. Learned rotation+BQ was not run. A semantic-supervised rotation
is unqualified without occurrence labels, while an unsupervised development-only
rotation remains unevaluated rather than prohibited.

Global FWHT is diagnostic only. It mixes all 256 source coordinates and its
first 64 transformed coordinates are not an original Matryoshka prefix.
Prefix/block FWHT independently transforms 64-coordinate blocks, so 64/128/256
boundaries remain physically progressive. The static encoder itself was not
Matryoshka-trained, so these are prefix mechanics rather than semantic-prefix
quality. One deterministic global-FWHT seed loses to the compatible form on
this proxy (30.96% versus 32.23%); this supplies no basis for a universal
mandatory global rotation, but does not falsify every global rotation.

## Hamming IVF and progressive I/O

The IVF reference partitions directly on raw BQ prefixes. It does not run float
k-means and then silently binarize centroids.

| Lists | Nonempty | Max list | CV | p95 candidates | p95 bytes | Float overlap | Exhaustive-Hamming overlap |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 256 | 94 | 2,806 | 9.26 | 4,179 | 34,456 | 7.23% | 13.67% |
| 512 | 152 | 2,329 | 11.09 | 3,671 | 30,392 | 6.05% | 8.98% |
| 1,024 | 219 | 2,110 | 14.28 | 3,401 | 28,232 | 5.18% | 7.91% |

Analytical staged-byte accounting charges 64 coarse bits for every probed
candidate, then the next 64 bits for at most 64 candidates and the final 128
bits for at most 32. No physical bitplane file was serialized, and the reported
4 KB pages are ideal contiguous `ceil(bytes/4096)` counts rather than measured
random/sequential I/O. Prefix buckets are extremely skewed and proxy fidelity is
poor. The IVF and Kanerva/SDM-inspired exhaustive Hamming variants are not
selected for this sparse static proxy and fixed `nprobe=8`; this does not
falsify an IVF built over a future learned dense representation.

## Gate and next lawful action

No 0.25M/1M/3M/5M model was trained. The reusable loader accepts a compiler
bundle only after recomputing the v2 manifest and all six stream identities. It
validates every resolved occurrence against the canonical entity registry,
retains canonical title/tier/Unicode offsets/source and span hashes, mints a
stable source-bound occurrence record ID, and carries explicit provenance IDs.
Missing, ambiguous, and redirect-cycle occurrences remain in the bundle's
quarantine view and are reconciled against unresolved surface-statistics
support; they are not silently filtered.

The source-document split is separate from any benchmark partition. Encoder,
rotation, and PQ fitting may use only `fit`; successive halving and model
selection may use only `calibration`; `holdout` is corpus-only qualification and
may never select a model. A source document observed in more than one split is
rejected. Deterministic supervision and ANN index manifests bind the compiler
bundle, canonical registry, stable occurrence IDs, split roles, and optional
encoder/index artifact hashes. The index contract can be serialized in a
blocked/not-built state. Adversarial fixtures exercise that state; no production
supervision or index manifest is claimed for the post-cap proxy measurement.

Source-document holdout, unseen-mention-surface, head/torso/tail, ambiguity, and
real-hard-negative semantic evaluation still require an actually supplied,
qualified occurrence-level substrate. The mechanics reference supports the full
raw/global/prefix BQ, PQ-ADC 8/16-byte, int8, Hamming, IVF 256/512/1024, and
progressive bitplane mechanics once those rows arrive.

The int8 result preserves 98.24% of this static float proxy's top-16 only; it is
not 98.24% semantic accuracy. The only justified next step for this lane is to
load an authenticated compiler export through this gate and inspect its
readiness statistics. Until then,
`SEMANTIC_ANN_NOT_JUSTIFIED` is the scoped lane result; the integrated system
must not count these mechanics as a new semantic channel or rerun the 695-state
policy gate on them.

## Reproduction

```bash
PYTHONPATH=src python scripts/droid/v12_semantic_ann_qualify.py \
  --candidate-diagnostic /external/candidate-diagnostic-397k.jsonl.gz \
  --candidate-manifest /external/candidate-diagnostic-397k.manifest.json \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output reports/droid/v12/semantic-encoder-ann-ablation.json \
  --query-limit 64
```
