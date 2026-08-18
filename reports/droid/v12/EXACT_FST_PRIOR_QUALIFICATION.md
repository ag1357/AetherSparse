# Mission 7 exact FST/prior channel qualification

## Decision

`EXACT_FST_CHANNEL_IMPLEMENTED_FULL_CORPUS_DATA_REQUIRED`

The immutable exact-address channel is implemented and validated. The available
397k source is a targeted post-cap candidate diagnostic, so this result is a
real-data serialization and title-transfer measurement—not a global address
recall claim. Evaluation and final-held rows were verified against the
authenticated replay and excluded before evidence construction.

## Data and integrity

| Measure | Result |
|---|---:|
| Authenticated replay bundle | `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246` |
| Diagnostic SHA-256 | `8dfb6c9a723a66d9dfd7d24a102a719a87b590a457fc1bab505cced771d57158` |
| Development cases / candidate occurrences | 271 / 20790 |
| Tuning cases / candidate occurrences | 414 / 32117 |
| Sealed rows entering compiler | 0 |
| Benchmark labels/answers used | no |

## Measured targeted development index

| Measure | Result |
|---|---:|
| Surfaces / entities / postings | 4867 / 4913 / 4913 |
| Total serialized bytes | 1542711 |
| Address core bytes excluding provenance sidecar | 967765 |
| Header / dictionary / postings / provenance bytes | 1698 / 487420 / 478647 / 574946 |
| Bytes per surface | 316.974 |
| Collision surfaces | 46 |
| Maximum postings for one surface | 2 |
| Self round-trip recall@1/4/8/16/32 | 0.990637 / 1.000000 / 1.000000 / 1.000000 / 1.000000 |
| Tuning title-transfer recall@1/4/8/16/32 | 0.236203 / 0.237822 / 0.237822 / 0.237822 / 0.237822 |
| Root SHA-256 | `1964e0f0a3571de3bd09c6199561e4b32cd69c6804fd6dd74a5d79880aef6033` |
| File SHA-256 | `c5a373e41703367233f067bd18d94d1f90fb3bcc026643842805dba73417ba94` |

Priors are not quantized: each posting stores integer support and each group
stores total support, so `P(entity|surface)` is reconstructed losslessly. This
preserves recall and every support-based ranking while using fewer bytes than a
stored floating-point prior.

## Runtime contract

- Normalized UTF-8 bytes traverse an immutable path-compressed acyclic byte FST.
- A terminal state returns a posting byte offset and full address distribution.
- Canonical entity IDs remain authoritative; title collisions are retained.
- Title/redirect/alias/anchor support, source diversity, ambiguity entropy,
  unresolved mass, and provenance references are represented explicitly.
- A caller cap reports omitted candidate count and probability mass; it cannot
  silently convert truncation into confidence.
- Every section and the complete file are content-addressed and verified.

## Limitation and next dependency

The diagnostic has no full title registry, aliases, redirects, anchors, pre-cap
pool, or channel provenance. It therefore cannot qualify global FST size,
mention recall, or semantic-address recall. Lane A's full-corpus exporter can
feed the same `AddressEvidence` compiler without changing the runtime format.
