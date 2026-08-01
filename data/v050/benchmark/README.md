# AetherSparse v0.5 natural-query benchmark

`INDEPENDENT_NATURAL_QUERY_SET_V050_R1` is a new qualification series. It is
not the lost v0.4.1 R4 benchmark and does not reuse that benchmark's identity.

The committed gold set contains 2,050 unique questions across all 19 required
categories. Three isolated author invocations produced question drafts without
runtime outputs. A fourth process independently reopened the checksum-pinned
corpus, reproduced candidate offsets, adjudicated accepted answers and
dispositions, and froze the gold. A fifth process produced the gold-free runtime
input. A sixth process audited provenance directly against the read-only corpus.

Source corpus:

* series: `simplewiki_real_corpus_v050_20260701_e7a60c622d86dd01`
* pack: `simplewiki-v050-20260701-10k-final.sqlite`
* pack SHA-256: `cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5`
* pack storage: external qualification artifacts, not ordinary Git

Frozen outputs:

* `INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json`: complete adjudicated gold
* `INDEPENDENT_NATURAL_QUERY_SET_V050_R1.blind-input.json`: runtime questions
  with no gold disposition, answer, entity, facet, claim, or evidence fields
* `INDEPENDENT_NATURAL_QUERY_SET_V050_R1.source-map.json`: compact immutable
  source identity map; source text remains in the checksum-pinned SQLite pack
* `INDEPENDENT_NATURAL_QUERY_SET_V050_R1.manifest.json`: counts, corpus identity,
  content hash, and role-separation record

The complete benchmark file SHA-256 is
`1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113`.
Its canonical case-content SHA-256 is
`c4a8f45b30fa592d9ae7e01d0c456e95b7361e73575e97f97dbcb6da397cb673`.

Reproduction:

```bash
uv run python scripts/benchmark_authoring/run_pipeline.py \
  --corpus /path/to/simplewiki-v050-20260701-10k-final.sqlite \
  --work-directory /path/to/external-work/benchmark-v050-r1 \
  --output-directory data/v050/benchmark/reproduced
```

Author drafts are intermediate audit objects and remain outside Git. Their
hashes and process identities are recorded in `roles-and-invocations.json`.
