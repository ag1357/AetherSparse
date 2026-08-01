# AetherSparse v0.5 real-corpus reproduction

This recipe builds the new v0.5 frozen series. It does not recover or reproduce
the unavailable v0.4.1 objects.

## Identity

- Source project: Simple English Wikipedia
- Dump date: 2026-07-01
- Parser: `mediawiki-xml-v050-distinct-source-pages-v1`
- Normalization: `nfkc-html-punctuation-whitespace-v050-v1`
- Pack format: `aethersparse-flat-structured-sqlite-v050-1`
- SQLite schema version: 500
- Namespace: 0
- Selection order: official dump order, nonempty latest revision text
- Chunk bound: 480 Unicode code points

## Commands

Use an artifact directory outside the Git checkout:

```bash
export AETHERSPARSE_ARTIFACT_ROOT=/path/to/checksum-pinned-artifacts

python scripts/acquire_simplewiki_dump.py \
  --dump-date 20260701 \
  --status https://dumps.wikimedia.org/simplewiki/20260701/dumpstatus.json \
  --output-dir "$AETHERSPARSE_ARTIFACT_ROOT/source" \
  --progress-log "$AETHERSPARSE_ARTIFACT_ROOT/source/acquisition.progress.jsonl" \
  --manifest-output "$AETHERSPARSE_ARTIFACT_ROOT/source/simplewiki-20260701.source.json"

python scripts/build_simplewiki_pack.py \
  --dump "$AETHERSPARSE_ARTIFACT_ROOT/source/simplewiki-20260701-pages-articles.xml.bz2" \
  --source-manifest data/real_corpus/v050/simplewiki-20260701.source.json \
  --output "$AETHERSPARSE_ARTIFACT_ROOT/packs/simplewiki-v050-20260701-10k-final.sqlite" \
  --manifest-output "$AETHERSPARSE_ARTIFACT_ROOT/packs/simplewiki-v050-20260701-10k-final.manifest.json" \
  --articles 10000

python scripts/build_simplewiki_pack.py \
  --dump "$AETHERSPARSE_ARTIFACT_ROOT/source/simplewiki-20260701-pages-articles.xml.bz2" \
  --source-manifest data/real_corpus/v050/simplewiki-20260701.source.json \
  --output "$AETHERSPARSE_ARTIFACT_ROOT/packs/simplewiki-v050-20260701-50k-final.sqlite" \
  --manifest-output "$AETHERSPARSE_ARTIFACT_ROOT/packs/simplewiki-v050-20260701-50k-final.manifest.json" \
  --articles 50000
```

The builder streams the bzip2/XML input and does not materialize decompressed
XML. It refuses to overwrite an activated pack, commits only into a temporary
build object, runs SQLite integrity/foreign-key/source-binding checks, vacuums
the pack, hashes it incrementally, and then atomically activates it.

## Source-identity invariant

`documents.source_text_sha256` is deliberately indexed but not unique. The
immutable source identity is `(wiki_page_id, revision_id)`, represented by a
stable `document_id`. Therefore two redirect pages with identical wikitext
remain two documents. Regression tests assert both distinct IDs and their equal
content hashes.

Chunk offsets use Unicode-code-point positions into the exact stored page
wikitext. `RealCorpusPack.source_binding()` re-slices the immutable page and
checks the chunk hash and document hash before returning a binding.

## Read-only controller interface

`aethersparse.real_corpus.RealCorpusPack` opens SQLite in immutable read-only
mode and enforces bounded result limits. It exposes:

- `title_lookup`, `alias_lookup`, and `anchor_lookup`;
- `search_chunks` and `chunks_for_documents`;
- `document`, `chunk`, and `source_binding`;
- `metadata`, `last_trace`, and `workload_trace`.

The workload trace records operation, result limit, index probes, returned
payload bytes, estimated payload blocks, SQLite page size, and elapsed time.
It is logical instrumentation, not a claim of physical cold-storage reads.

Large dumps and SQLite packs are external artifacts. Only source identities,
hashes, compact manifests, code, tests, and this recipe belong in ordinary Git.
