# Frozen real-corpus recovery status

The exact frozen question inputs survived workspace maintenance:

| Input | SHA-256 | Status |
|---|---|---|
| `questions.json` | `77565d8fa6416a11ed3e5079c173e6d14348836162782699543a3608d0ba440a` | verified |
| `scaling_questions.json` | `6aebb09c9272d9e046cff7e614de4edbc86147c1823a0bf5e32c38647c8d99e7` | verified |

The frozen 1k, 10k and 50k SQLite pack bytes and the source XML dump are not
present in the retained release archives or current workspace. They must not be
silently regenerated from a moving `latest` dump because that would create a
different corpus identity.

`aethersparse cells qualify-frozen` now performs a complete fail-closed preflight
before running any topology. It verifies all three pack byte sizes and SHA-256
hashes, SQLite integrity, article/chunk counts, and both question files. No
partial result is written if any input is missing or changed.

Expected frozen pack identities:

| Scale | Articles | Chunks | Bytes | SHA-256 |
|---|---:|---:|---:|---|
| 1k | 1,163 | 32,808 | 76,546,048 | `3adebd4168e31fb9e1ceb72cf5cb07e2b4a9024648cab69792a86a5ca218f719` |
| 10k | 10,527 | 220,378 | 557,326,336 | `7c0eafd970c6e69f2f9f6d5b2bbc55c2e9990cf48e08046fe76bbfebc89b159e` |
| 50k | 51,040 | 664,403 | 1,591,808,000 | `7ff42a493f72487c76127ac936f782d79621624631e8e7ca5fc3b71877704af8` |

Exact command after restoring those three files under `data/real_corpus/`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run aethersparse cells qualify-frozen \
  --manifest data/real_corpus/manifest.json \
  --corpus-root data/real_corpus \
  --max-documents 256 \
  --output reports/COGNITIVE_CELL_SCALING_QUALIFICATION.json
```

The pack bytes should be restored to artifact storage, not committed to the
source repository. A separate corpus repository would still be unsuitable for
these multi-gigabyte SQLite files under ordinary Git; use release/object storage
with checksum-pinned manifests if they are recovered.
