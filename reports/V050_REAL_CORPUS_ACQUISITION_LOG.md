# AetherSparse v0.5 real-corpus acquisition and build log

Status: `REAL_CORPUS_ACQUIRED_10K_50K_FROZEN`

This is a new v0.5 series. It uses the same independently verifiable upstream
dump object recorded by the lost v0.4.1 experiment, but it does not reuse the
lost series identity, parser identity, pack format, pack hashes, or release
identity.

## Acquisition escalation

1. Searched the inherited public worktree and available workspace paths for a
   surviving dump, SQLite pack, real-corpus builder, patch, or released v0.4.1
   object. The pruned v0.4.1 objects were not present.
2. Requested the official 2026-08-01 dump status. Result: HTTP 404; no complete
   object was available at that identity.
3. Requested the official 2026-07-20 dump status. Result: HTTP 404.
4. Requested the official `latest` status alias. Result: HTTP 404.
5. Requested the official 2026-07-01 status manifest. The `articlesdump` job was
   complete and resolved exactly one single-stream pages/articles object.
6. Acquired the archive with persistent partial-file continuation and retries.
   The repository now also contains a standard-library resumable downloader
   with Range requests, exponential backoff, timeouts, byte-count checks,
   official hash checks, a persistent JSONL progress log, and atomic activation.
7. Verified archive byte count, official SHA-1, official MD5, computed SHA-256,
   and bzip2 stream integrity before building.

## Frozen official source

- URL:
  `https://dumps.wikimedia.org/simplewiki/20260701/simplewiki-20260701-pages-articles.xml.bz2`
- Status:
  `https://dumps.wikimedia.org/simplewiki/20260701/dumpstatus.json`
- Compressed bytes: 351,744,161
- SHA-1: `e60e2ebad13467976ad7cd0d9bd0369bba0e8bc3`
- MD5: `211bfc6ac3120c097ba4fdec69c1d3e2`
- SHA-256:
  `541a2547b6cc72e91449719226d05181234cfadb2531a69faca1969245c8cb5d`
- bzip2 test: exit status zero

## New v0.5 series

Series: `simplewiki_real_corpus_v050_20260701_e7a60c622d86dd01`

- Source-manifest SHA-256:
  `2effe087f6b299100f6803970e062576dfc56b443eb1beaaca9dcd97adf4c3d3`
- Series-manifest SHA-256:
  `00575d3e9175f1e870d2a9ae999a987312d950cdffc991c96e2cd510dfd0d91b`

- Parser: `mediawiki-xml-v050-distinct-source-pages-v1`
- Normalization: `nfkc-html-punctuation-whitespace-v050-v1`
- Pack format: `aethersparse-flat-structured-sqlite-v050-1`
- SQLite schema: 500
- Chunk bound: 480 Unicode code points
- License: CC-BY-SA-4.0

| Scale | Documents | Chunks | Anchors | Redirects | Exact-bound records | Bytes | SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 10k | 10,000 | 147,549 | 779,447 | 2,349 | 939,345 | 725,901,312 | `cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5` |
| 50k | 50,000 | 449,460 | 1,718,196 | 14,551 | 2,232,207 | 2,015,846,400 | `8589cebca4a7dcf8eee6e936bb28f2ed8db7870672cc9b476f27ebe2ad89a7b4` |

Both packs passed SQLite `integrity_check`, foreign-key checks, exact source
slice checks, chunk-span hashes, and document hashes with zero binding failures.

The 50k pack was then rebuilt independently from the verified compressed source
into a second output path. Both builds were exactly 2,015,846,400 bytes and had
the identical SHA-256
`8589cebca4a7dcf8eee6e936bb28f2ed8db7870672cc9b476f27ebe2ad89a7b4`.
The deterministic byte-for-byte reproduction check therefore passed.

## Source-identity defect regression

The old failure mode made document content hashes unique, which collapsed
different pages when their wikitext was identical. v0.5 instead makes
`(wiki_page_id, revision_id)` unique and treats source-text SHA-256 as a
nonunique lookup index.

- 10k preserved 638 documents in 255 duplicate-text hash groups.
- 50k preserved 5,750 documents in 2,092 duplicate-text hash groups.
- A two-redirect fixture independently asserts distinct document IDs and equal
  content hashes.

No object from an earlier prepublication build attempt is included in the
series manifest. Those local operational objects are revoked and have no release
identity. Only the `*-final.sqlite` objects and hashes above are eligible inputs
to v0.5 qualification.

## Storage policy

The 351 MB source archive and 0.73/2.02 GB SQLite packs remain outside ordinary
Git under checksum-pinned external artifact storage. Git contains only code,
tests, identities, manifests, logs, and reproduction instructions. The full
SimpleWiki pack is intentionally deferred until 10k/50k architecture gates
justify its storage and build cost.
