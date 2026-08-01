# AetherSparse v0.4.1 — recovered qualification record

> Recovery notice
>
> This record preserves known results and identities only. The original v0.4.1
> source tree and generated artifacts are lost; historical hashes identify
> unavailable objects only. No reconstructed bytes may be labeled as the old
> release, and v0.4.1 remains an empirical record rather than a publishable
> source release.


Recovered from the completed July 31–August 1, 2026 qualification conversation
after automated scratch-workspace pruning. This document preserves known results,
identities, hashes, and publication state. It is **not** the lost source archive or
patch bundle and cannot validate substitute bytes against their old hashes.

## Publication state verified on August 1, 2026

- Public repository: `ag1357/AetherSparse`
- Default branch: `main`
- Latest public commit: `eac67c734eb18e46a57f77e4a7196666df8d330c`
- Public commit message: `Publish AetherSparse v0.4.0`
- Its parent: `99ff0cba3d93b8ba8e9e29eb775795d85867d157`
- No public branch matching v0.4.1 was found.
- GitHub reports no commit object for the lost v0.4.1 first commit
  `17ac2f1edf234e23efd6c38d21d3931e3eec9d7d` or final commit
  `3a0bbace08be32945708b5868f8cd069e056b4ca`.
- Therefore v0.4.0 was properly published, but the v0.4.1 qualification was only
  committed and packaged locally before workspace maintenance removed it.

## Final empirical decisions

- Cell-topology finding: `HKC_CELL_TOPOLOGY_NOT_JUSTIFIED`
- Architecture decision: `FLAT_HYBRID_RETRIEVAL_PREFERRED`
- Hardware decision: `NO_HARDWARE_PURCHASE_JUSTIFIED`

The retained direction was flat lexical retrieval with deterministic feature
fusion, exact structured evidence, pointer-copy realization, and fail-closed
verification. The tested exhaustive dense projection was not endorsed.

## Natural real-source qualification

Benchmark: `INDEPENDENT_NATURAL_QUERY_SET_V041_R4`

- 2,000 questions; 404 final-held questions; 23 required categories.
- 1,479 connected evidence components; largest component seven questions/articles.
- Six tuning/evaluation roles were question- and article-disjoint.
- Three deterministic authoring identities, adjudicator, and evaluator were distinct.
- Independent provenance audit: 2,564/2,564 source bindings passed.
- Benchmark questions SHA-256:
  `67760bce278be2a9ed5a57f7e38b6af9ba6d63b1a295dce69d22bac35b7200a4`
- Freeze SHA-256:
  `92337617b91b9b5249e5cb3d70bb9911803d976cd61916e08ea063f066eee7fd`
- Cross-agent audit SHA-256:
  `743b1035104a5efab07c719da8a4be347209554bfef102dc4c6a60de2d9823ad`

Complete constrained verified-RAG results:

| Metric | Overall | Final held-out |
| --- | ---: | ---: |
| Disposition-inclusive natural accuracy | 37.65% | 41.58% |
| Exact supported accuracy on answerable questions | 31.06% | 34.80% |
| Unsupported-claim rate | 0.00% | 0.00% |
| Silent wrong-entity rate among emitted answers | 25.86% | 26.45% |

Final-held system comparison:

| System | Article R@8 | Complete article R@8 | Evidence R@8 | Exact answerable | All-item accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| Flat lexical extractive | 82.77% | 68.58% | 74.32% | 45.61% | 33.42% |
| Deterministic feature fusion | 84.12% | 70.61% | 78.04% | 48.65% | 35.64% |
| Compact INT8 linear ranker | 84.12% | 70.95% | 79.05% | 48.65% | 35.64% |
| Gap-triggered targeted traversal | 84.12% | 70.95% | 79.39% | 48.65% | 35.64% |
| Constrained verified-RAG | 84.12% | 70.95% | 79.39% | 34.80% | 41.58% |

- Adversarial verifier: 2,811/2,811 deterministic mutations rejected.
- Final clarification precision/recall: 15.32%/95.00%.
- Final abstention precision/recall: 63.89%/52.27%.
- Comparison, two-source, three-to-six-source, conversational follow-up, and
  pronoun/coreference exact-answer categories all scored 0%.
- The earlier R3 benchmark was retained only as
  `FAILED_PROVENANCE_AUDIT_RETAINED_FOR_LINEAGE`: duplicate SQLite column names
  copied chunk hashes into 2,543/2,564 document-hash fields.

## Official corpus and progressive packs

Official source object:

- URL: `https://dumps.wikimedia.org/simplewiki/20260701/simplewiki-20260701-pages-articles.xml.bz2`
- Compressed bytes: 351,744,161
- Official SHA-1: `e60e2ebad13467976ad7cd0d9bd0369bba0e8bc3`
- SHA-256: `541a2547b6cc72e91449719226d05181234cfadb2531a69faca1969245c8cb5d`
- bzip2 integrity passed.
- Series identity: `series:7aca6afd0a8d279c755ec99a`
- Parser identity: `mediawiki-xml-v2-distinct-source-pages`
- Normalization: `nfkc-html-punctuation-whitespace-v1`

| Pack | Documents | Chunks | Bytes | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| 1k | 1,000 | 19,428 | 59,871,232 | `fe29b11a674e8f2cbbbbe247fcccac05665249e0ebb9fcf53abc8071e9f59d4e` |
| 10k | 10,000 | 147,549 | 498,716,672 | `c6cd4797010332a898694354083ef63b2e72f447b481e6851fea216f33d6bf78` |
| 50k | 50,000 | 449,460 | 1,414,991,872 | `dbe40e23854e502d8b9d9c903d09e94c356fbdf901424206dba06dd9ee0cea2f` |
| 100k | 100,000 | 783,109 | 2,418,515,968 | `d39f5a4bcfa26ce87d114c75150b97f6ea8adfdd637c8c751833aca2f27f461b` |
| full | 397,196 | 2,799,975 | 8,269,697,024 | `a7940e47b7e80a0d00ad88b4ec45a6efbb99a23d135ac1e7c492167150ba6db3` |

The first 1k attempt was revoked because a `UNIQUE content_hash` constraint
collapsed distinct pages with identical wikitext, especially redirects. The fixed
builder regression-locked distinct source page IDs.

Graph-derived regression results, not independent qualification:

| Scale | Top-k article | Top-k span | Iterative article | Iterative span |
| --- | ---: | ---: | ---: | ---: |
| 1k | 70.33% | 14.67% | 72.33% | 14.67% |
| 10k | 79.67% | 14.67% | 83.67% | 17.33% |
| 50k | 75.00% | 11.67% | 78.00% | 13.67% |

Only 22.33% of old gold chunk IDs existed under the new build identity, so those
span figures were diagnostic only.

## Structured knowledge plane

| Scale | Canonical entities | Redirects | Aliases | Anchors | Relation families | Claims | Prose records | Verified bindings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1k | 749 | 251 | 931 | 9,467 | 1,155 | 5,633 | 362 | 15,462 |
| 10k | 7,651 | 2,349 | 9,704 | 355,274 | 3,700 | 55,860 | 4,377 | 415,511 |
| 50k | 35,449 | 14,551 | 49,212 | 929,823 | 6,557 | 233,119 | 21,469 | 1,184,411 |

All reported anchor/claim/record bindings rebound to immutable source hashes and
offsets. Structured records augmented, rather than replaced, original chunks.

## Real-corpus topology result

| Topology + VSA | 10k cell R@8 | 50k cell R@8 | Degradation | 50k p95/max cell | 50k overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Category | 66.5% | 57.0% | -9.5 pt | 11 / 256 | 2.22x |
| Entity/relation | 74.5% | 71.0% | -3.5 pt | 140 / 256 | 33.69x |
| Semantic-topic | 66.0% | 55.5% | -10.5 pt | 7 / 256 | 3.83x |
| Hybrid | 73.0% | 70.0% | -3.0 pt | 50 / 256 | 39.74x |

All VSA candidate pools saturated the 256-cell cap. At 10k:

| Method | Article@32 | Exact evidence recall | Mean source blocks |
| --- | ---: | ---: | ---: |
| Flat lexical | 73.26% | 43.41% | 127.2 |
| Flat lexical+dense projection | 68.99% | 44.57% | 117.2 |
| Category + VSA | 64.73% | 39.92% | 88.8 |
| Entity/relation + VSA | 39.53% | 26.36% | 105.8 |
| Semantic-topic + VSA | 55.04% | 35.27% | 64.8 |
| Hybrid + VSA | 39.92% | 26.36% | 104.1 |

Category cells reduced reads by 30.2% but lost 8.53 article@32 points and 3.49
evidence-recall points, so they failed the equal-accuracy work-reduction gate.

## Optional mechanism decisions

- VSA/HDC: not retained; it did not rescue the failing topology or satisfy the
  equal-accuracy work gate.
- Generated addressing: not retained; invalid IDs were rejected and fallbacks
  worked, but no trained predictor existed, so recall/cost gates were null.
- Targeted traversal: not retained; +2.36 complete-article points and no exact
  answer gain missed the required eight-point gate.
- Compact INT8 linear ranker: not retained over deterministic feature fusion;
  exact-answer accuracy was equal.
- Optional cross-encoder experiment: not retained.

## Binary pack and edge profile

- Addressed 10k hybrid pack bytes: 486,271,178
- Pack SHA-256: `29299c6c371a80dee3138e15b93ec375504b16d9f4af3d490a27579c4ab862fd`
- Routed-profile SHA-256: `e815160dc2e5c0724e231b1e0f93f1947d5603b18049621ec9e78650b0dac7fe`
- Cell blocks: 48,638
- Routing hot set: 11,951,296 bytes
- Mean/p95 total bytes read: 12,618,125 / 13,789,888
- Storage reads per query: 10
- Address overlap: 59.5967x
- Documents requiring source-index fallback: 5,608/10,000

A frozen 20-query host prefix under `POSIX_FADV_DONTNEED` advice measured:

- Mean/p95 latency: 273.964 / 575.733 ms
- Process physical-read bytes: 632,967,168
- Host peak RSS: 171,084 KiB

This advice did not prove eviction from every cache and was not a board measurement.

Digital-twin p95 projections for the rejected cell workload:

| Profile | Projected p95 |
| --- | ---: |
| P4 Pico microSD | 6,621 ms |
| Future P4 eMMC | 1,349 ms |
| Core1106 eMMC | 690 ms |
| RT700-class | 2,222 ms |
| Representative low-power FPGA | 1,169 ms |

Only the Core1106 profile met one second, but purchase was rejected because the
answer-quality and cell-topology gates failed, no target-board measurements existed,
and the architecture was not frozen.

## Validation completed before loss

- 160 tests passed.
- Ruff passed repository-wide.
- Strict mypy passed across 71 source files.
- The nine-patch series was independently reapplied to public `eac67c7` and
  reproduced expected tree `d39810c876f0c00cc46e29211550faf273a937e5`.

## Lost local commit chain

1. `17ac2f1edf234e23efd6c38d21d3931e3eec9d7d` — Preserve v0.4.1 qualification checkpoint
2. `f7383683fa4ba03d837cf88b86ca10bfd000c0e5` — Integrate bounded v0.4.1 architecture changes
3. `b2e14d2c06d7dfed11f91c6f5873651ee1b3e2d8` — Measure bounded binary pack workloads
4. `a247ce84bbd99e89ea1586c3c0532081855dc7c0` — Qualify independent natural grounded answers
5. `f395958e44bc63d2c95d053237e016bdd6a4b90a` — Acquire and freeze exact real corpus series
6. `7eb82f2f4337ad96e3dad1bda1c4d000ffb9340c` — Record verified progressive corpus series
7. `eb4443f8f2d5335b836cf5ab3c507b52955bb52b` — Falsify cognitive-cell topology on real corpus
8. `3261cb3f0c3a6981f3aab6cc708a94ae7cff6b2a` — Seal audited R4 natural-query qualification
9. `3a0bbace08be32945708b5868f8cd069e056b4ca` — Issue final v0.4.1 architecture and hardware decisions

Expected final tree: `d39810c876f0c00cc46e29211550faf273a937e5`

Lost local tag: `v0.4.1-qualification`

## Lost release objects and their former hashes

These hashes identify the old objects only; the objects themselves were not
recovered from GitHub or Library.

| Lost object | Bytes | SHA-256 |
| --- | ---: | --- |
| `AetherSparse-v0.4.1-real-corpus-qualification.tar.gz` | 720,595 | `322881bb3ef7465ac2ea24ff6310ab405cd3c231507a631af06f177589809d06` |
| `AetherSparse-v0.4.1-format-patch-series.tar.gz` | 252,311 | `1d77ac81294860e7f6dbed0c2832381c95f55c8867fd5b5a87abe7fde9203bae` |
| `AetherSparse-v0.4.1-against-eac67c7.patch` | 899,148 | `d613c841be5d08fe6ac2be5f01fe15544d5d87ed2cae1bcbdf7aa9b0191d92b7` |

## Recovery boundary

The empirical record and all known identities are recoverable. The exact v0.4.1
source tree, nine patches, benchmark JSON objects, progressive SQLite packs, and
binary `.aeth` pack are not recoverable from hashes or the remaining public Git
history. Recreating them requires rebuilding from public v0.4.0 and rerunning the
qualification; recreated bytes and commit hashes must receive a new recovery
identity rather than being mislabeled as the lost originals.
