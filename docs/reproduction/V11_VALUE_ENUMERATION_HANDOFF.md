# Mission 6 targeted value-enumeration handoff

This is the minimal corpus-host continuation for the 89 Mission 5
`VALUE_NOT_ENUMERATED` training replicas. It does not run retrieval, training,
evaluation, final-held, or a broad corpus battery.

The Work-side diagnostic proves that the retained replay is missing the fields
needed to distinguish 21 replicas among source-chunk absence, sentence-region
pruning, deduplication, value-cap removal, and document rebinding. Specifically,
the replay does not retain:

- complete selected chunk text and chunk offsets;
- every scored sentence/region before the runtime top-eight region cut;
- compiler matches before type/page caps over the full source document;
- runtime matches before deduplication and the per-chunk/final value caps;
- a document-level exact-surface rebinding result.

Use the existing three tier packs and the already-exported certified replay:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v11-value-uv uv run python \
  scripts/droid/v11_value_diagnostic.py \
  --replay-bundle /root/work/v10/controller-replay-3tier \
  --reachability-report reports/droid/v10/mission5-real-reachability.json.gz \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --pack 10k=/root/work/artifacts/packs/selector-10k-p3.sqlite \
  --pack 25k=/root/work/artifacts/packs/selector-25k-p3.sqlite \
  --pack 397k=/root/work/artifacts/packs/selector-full-p3.sqlite \
  --output /root/work/v11/value-enumeration-diagnostic-v11.json.gz \
  --manifest-output /root/work/v11/value-enumeration-diagnostic-v11.manifest.json
```

The command verifies the replay bundle before reading cases, selects only the
certified development/tuning residual keys, and rejects protected/non-training
replay rows. For each affected tier replica it reads only the eight already
ranked chunk IDs from the tier pack and the exact gold source documents. It
records the complete selected chunk text, region ranks, pre-dedup/post-dedup and
pre-cap/post-cap values, exact document rebinding, and full-page compiler
boundary trace.

Return only the two output files. Do not return the SQLite packs, caches, or a
new corpus battery. The manifest contains source and output SHA-256 identities.
