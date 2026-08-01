# AetherSparse v0.5 flat structured substrate

This package reconstructs only the retained v0.4.1 direction: flat retrieval,
deterministic feature fusion, structured claims, and exact immutable evidence. It
does not reinstate cognitive-cell routing, VSA routing, generated addresses,
overlapping cell packs, traversal, or the rejected linear ranker.

## Source invariants

- `document_id` is derived from namespace, MediaWiki page ID, and revision ID.
  It is never derived from the content hash.
- `source_sha256` is an integrity property, not a uniqueness constraint. Distinct
  pages containing identical redirect text remain distinct documents.
- Every chunk, heading, anchor, redirect target, and claim evidence record has an
  exact `SourceBinding` with Unicode character offsets, UTF-8 byte offsets, source
  and surface SHA-256 values, page ID, revision ID, and document ID.
- Claims with repeated evidence text must provide explicit offsets. The builder
  rejects ambiguous alignment rather than choosing an occurrence.
- `validate_source_bindings()` independently reproduces all source hashes,
  surface hashes, byte counts, and coordinates.

## Structured records and indexes

`StructuredSubstrateBuilder` accepts immutable `SourcePage` records, optional
adjudicated `ClaimSeed` records, explicit aliases, and entity types. It compiles:

- canonical entity identities;
- title, redirect, anchor, and explicit aliases;
- redirects and resolved anchor-text mappings;
- headings and exact chunks;
- proposition, event, date, quantity, and quotation claims;
- lexical, title, heading, phrase, relation-family, and entity postings.

`FlatHybridRetriever.retrieve(RetrievalRequest)` uses a fixed integer fusion
policy over those postings. It exposes its lexical, field, phrase, proximity,
alias, redirect, anchor, entity, relation, answer-type, and temporal features for
every result. Candidate and result bounds are part of the request schema.

## Real-corpus bridge

`iter_source_pages_from_sqlite(path, document_ids=None, batch_size=256)` opens the
new real-corpus SQLite pack read-only and yields pages in bounded batches.

`substrate_metadata_from_sqlite(path, build_command=...)` transfers the new series,
dump checksum, parser, and normalization identities without inventing values.

`build_selected_substrate_from_sqlite(...)` builds an explicitly bounded
evaluation substrate. It requires selected document IDs or a maximum document
count so the host reference implementation cannot accidentally materialize a
full corpus.

## Flat binary format

`write_flat_binary_pack()` writes `AETHERSPARSE_FLAT_STRUCTURED_PACK_V1` files.
The header contains update metadata, the source pack manifest hash, a root hash,
and a directory of SHA-256-pinned sections. Documents, bindings, chunks, claims,
and each posting family are placed in deterministic content-addressed shards.

`FlatBinaryPackReader.query_sections()` maps query terms, relation families,
entity IDs, document IDs, and claim IDs to only their required shards. It verifies
each shard before returning bytes and reports storage reads and bytes read. A
declared maximum-section bound fails before any payload read. This format is a
flat structured pack; it has no overlapping cell topology.

## Validation

```bash
PYTHONPATH=src pytest tests/substrate -q
ruff check src/aethersparse/substrate tests/substrate
mypy src/aethersparse/substrate
```
