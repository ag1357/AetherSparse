# V12 semantic encoder and ANN reproduction

The qualifier accepts the external 397k candidate diagnostic only after its
manifest SHA-256 passes. It discards evaluation/final-held rows and builds the
static title index from development; tuning contributes only a fixed query
view. It rejects duplicate case IDs within or across partitions and requires
every development/tuning diagnostic partition to match the benchmark. It never
consumes accepted answers or gold entity IDs.

```bash
PYTHONPATH=src python scripts/droid/v12_semantic_ann_qualify.py \
  --candidate-diagnostic /path/to/candidate-diagnostic-397k.jsonl.gz \
  --candidate-manifest /path/to/candidate-diagnostic-397k.manifest.json \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output reports/droid/v12/semantic-encoder-ann-ablation.json \
  --query-limit 64
```

Expected external identity:

- candidate diagnostic gzip:
  `8dfb6c9a723a66d9dfd7d24a102a719a87b590a457fc1bab505cced771d57158`
- benchmark:
  `1e8b89427898df3c3e5efef55135192cf6d48240f0f568c95eb1570470ead113`

The generated JSON is aggregate-only. Do not commit the external diagnostic,
query rows, embeddings, PQ codebooks, or title index. A later learned run must
first load a verified v2 compiler export with `load_compiler_supervision` and
pass `training_readiness`. Corpus source splits are independent of benchmark
partitions: only `fit` may fit encoder/rotation/PQ parameters, only
`calibration` may drive successive halving or model selection, and `holdout` is
corpus-only qualification. A source document may occur in only one source split.

The loader recomputes the compiler manifest and all stream identities before it
reads supervision. It validates canonical ID/title pairs against the entities
registry and retains exact offsets, corpus tier, source/span hashes, stable
occurrence record IDs, and provenance IDs. Non-canonical occurrences remain in
`quarantined_occurrences` and their counts must reconcile with unresolved
surface-statistics support.

After loading, serialize the identity-bound contracts before any learned run:

```python
from pathlib import Path

from aethersparse.addressing.semantic_ann import (
    load_compiler_supervision,
    write_semantic_index_manifest,
    write_semantic_supervision_manifest,
)

bundle = load_compiler_supervision(Path("/path/to/compiler-v2-tier"))
supervision_sha256 = write_semantic_supervision_manifest(
    bundle, Path("/output/semantic-supervision-manifest.json")
)
write_semantic_index_manifest(
    bundle,
    Path("/output/semantic-index-manifest.json"),
    supervision_manifest_sha256=supervision_sha256,
)
```

For a readiness-blocked bundle, the second manifest is explicitly
`NOT_BUILT_TRAINING_READINESS_GATE`; a ready bundle with no artifact identities
is `NOT_BUILT_NO_ARTIFACT`. Neither state fabricates an index.

All reported overlaps are compression fidelity against an untrained sparse
static hash, not semantic accuracy. PQ is only a 16-centroid partial screen;
FWHT/IVF conclusions remain proxy-scoped; progressive I/O is analytical staged
byte accounting and does not claim a physically serialized layout or hardware
measurement.
