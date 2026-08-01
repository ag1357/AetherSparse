# v0.5 SQLite controller provider

`SQLiteControllerProvider` is the lazy boundary between the v0.5 structured
controller and a checksum-pinned flat real-corpus pack. It opens the canonical
SQLite object read-only and immutable. It rejects packs that do not contain the
`documents`, `chunks`, `chunks_fts`, `aliases`, `redirects`, and `anchors` tables
or do not declare schema version 500.

It does not materialize the corpus registry. Canonical controller IDs are
derived from normalized titles using the structured-substrate contract:

```text
as:v050:entity:{sha256(nfkc_casefolded_title)[:24]}
```

The provider retains only the entity-to-document mappings encountered during a
query. It resolves mentions through title, redirect, explicit alias, anchor, and
a bounded contextual fuzzy stage. Lowercase aliases are discovered with a
bounded longest-ngram lookup. Unknown surfaces keep their exact normalized-query
offsets and are never converted to invented entity IDs. An unknown entity causes
retrieval to return zero candidates and the controller to select
`OUT_OF_CORPUS`.

## Exact evidence contract

Retrieval uses FTS plus title-linked chunks; it does not use cognitive cells,
generated addresses, hyperlink-frontier traversal, or overlapping packs. Each
candidate answer is an exact substring of `documents.raw_wikitext`. Before an
`ExactSourceSpan` is returned, the provider re-reads that substring by document
ID and absolute code-point offsets. The returned span records the source URL,
revision, document ID, offsets, text, and SHA-256.

The extractor is deliberately small and bounded. It recognizes query-relevant
definitions, dates, quantities, quotations/attributions, links, and selected
event/process passages. Infobox values are only preferred when their field name
matches the requested relation. Birth and death cues compete locally so a nearby
but directionally wrong date is demoted. A row contributes at most four exact
values.

## Bounds and workload fields

- Public evidence limits are 1–64 records.
- FTS and entity candidate pools are capped at 64 rows per retrieval.
- Fuzzy title comparison scans at most 128 length- and prefix-bounded rows and
  returns at most eight candidates.
- Implicit lowercase mention discovery considers at most 32 ngrams, returns at
  most four non-overlapping mentions, and uses two index probes.
- Active graphs remain separately bounded by the controller implementation.

`ProviderWorkload` reports candidate rows, evidence records, index probes,
payload bytes, SQLite page size, estimated blocks, source-document hashes, and
host latency. Bytes are measured serialized row plus selected exact-span payload;
blocks are the ceiling of those bytes divided by the SQLite page size. They are
host SQLite workload measurements, not physical-media traces. The edge profiler
must report physical/cold-cache measurements separately.

## Qualification runner

`scripts/run_v050_qualification.py` verifies the frozen benchmark hash, pack byte
count, and (unless explicitly skipped) the complete pack SHA-256. It executes
one outcome for every case and every ablation:

1. flat lexical extractive;
2. deterministic feature fusion;
3. fusion plus contextual entity linker;
4. fusion plus query frame and facet prediction;
5. fusion plus exact evidence graph;
6. full extractive cognitive controller;
7. full controller plus deterministic constrained realizer;
8. verified-RAG comparator.

The first seven are executable deterministic configurations. The constrained
realizer is rerun independently even though no neural reworder is configured.
No verified-RAG model is bundled; that comparator therefore emits explicit
fail-closed `ABSTAIN` outcomes and is reported as
`NOT_CONFIGURED_FAIL_CLOSED`, rather than being simulated. The runner also
invokes the held-out adversarial verifier experiment when that supplement is
installed. Its learned score can only veto a deterministically verified answer.

Passage-sized benchmark evidence IDs are projected only during grading when a
runtime exact span is wholly contained in the same frozen document and gold
span. Gold answers, required entities, and gold evidence do not enter query
framing, linking, retrieval, selection, or disposition.

Example 10k qualification:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_v050_qualification.py \
  --pack /artifact/packs/simplewiki-v050-20260701-10k-final.sqlite \
  --pack-manifest /artifact/packs/simplewiki-v050-20260701-10k-final.manifest.json \
  --benchmark /artifact/benchmark-v050-r1/benchmark.json \
  --output /artifact/reports/v050-10k-qualification.json \
  --outcomes /artifact/reports/v050-10k-outcomes.json
```

Use `--case-limit` only for smoke tests. Such a report is marked incomplete.
`--skip-pack-sha256` exists for local diagnostics and must not be used for a
qualification report. Large packs and outcome files remain outside ordinary
Git; Git stores the runner, schemas, manifests, reports, and reproduction
commands.
