# Reproduce the AetherSparse v0.5 qualification

The v0.5 series is distinct from the lost v0.4.1 objects. Large dumps, SQLite
packs, outcomes, and edge traces remain outside ordinary Git and are activated
only after their manifests and SHA-256 values verify.

## 1. Acquire and build

```bash
uv run python scripts/acquire_simplewiki_dump.py \
  --dump-date 20260701 \
  --status https://dumps.wikimedia.org/simplewiki/20260701/dumpstatus.json \
  --output-dir /artifacts/source \
  --progress-log /artifacts/source/simplewiki-20260701.progress.log \
  --manifest-output /artifacts/source/simplewiki-20260701.source.json

uv run python scripts/build_simplewiki_pack.py \
  --dump /artifacts/source/simplewiki-20260701-pages-articles.xml.bz2 \
  --source-manifest /artifacts/source/simplewiki-20260701.source.json \
  --articles 10000 \
  --output /artifacts/packs/simplewiki-v050-20260701-10k-final.sqlite \
  --manifest-output /artifacts/packs/simplewiki-v050-20260701-10k-final.manifest.json

uv run python scripts/build_simplewiki_pack.py \
  --dump /artifacts/source/simplewiki-20260701-pages-articles.xml.bz2 \
  --source-manifest /artifacts/source/simplewiki-20260701.source.json \
  --articles 50000 \
  --output /artifacts/packs/simplewiki-v050-20260701-50k-final.sqlite \
  --manifest-output /artifacts/packs/simplewiki-v050-20260701-50k-final.manifest.json
```

Build the 50k pack a second time and compare its complete SHA-256 before using
it as reproducibility evidence. Distinct pages with identical source text must
remain distinct document identities.

Build the compact canonical flat binary fixture without committing its bytes:

```bash
uv run python scripts/build_v050_binary_pack.py \
  --pack /artifacts/packs/simplewiki-v050-20260701-10k-final.sqlite \
  --pack-manifest data/real_corpus/v050/simplewiki-v050-20260701-10k-final.manifest.json \
  --documents 256 \
  --chunk-chars 1024 \
  --shards 32 \
  --output /artifacts/binary/flat-structured-256-final-r1.aeth \
  --manifest-output /artifacts/binary/flat-structured-256-final-r1.manifest.json
```

The tracked manifest must embed the canonical series and parent pack SHA-256.
Rebuild to a second path and require byte-for-byte equality before activation.

## 2. Rebuild and audit the benchmark

```bash
uv run python scripts/benchmark_authoring/run_pipeline.py \
  --corpus /artifacts/packs/simplewiki-v050-20260701-10k-final.sqlite \
  --work-directory /artifacts/benchmark-work \
  --output-directory /artifacts/benchmark-reproduced

uv run pytest -q tests/benchmark_authoring/test_frozen_r1.py
```

Question authoring, exact-source adjudication, gold-free runtime input,
evaluation, and provenance auditing use distinct process identities. Authors
never receive controller output.

## 3. Run the complete matched ablation

```bash
for scale in 10k 50k; do
  uv run python scripts/run_v050_qualification.py \
    --pack /artifacts/packs/simplewiki-v050-20260701-${scale}-final.sqlite \
    --pack-manifest /artifacts/packs/simplewiki-v050-20260701-${scale}-final.manifest.json \
    --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
    --evidence-limit 32 \
    --output /artifacts/qualification/V050_${scale}_QUALIFICATION_R2.json \
    --outcomes /artifacts/qualification/V050_${scale}_OUTCOMES_R2.json
done
```

Do not pass `--skip-pack-sha256` for qualification evidence. A complete run
contains one outcome for every benchmark-case/system pair across all eight
frozen systems.

## 4. Hard-negative and edge ablations

```bash
uv run python scripts/run_v050_hard_negative_ablation.py \
  --pack /artifacts/packs/simplewiki-v050-20260701-10k-final.sqlite \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output /artifacts/qualification/V050_HARD_NEGATIVE_ABLATION.json

uv run python scripts/build_v050_edge_queries.py \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --outcomes /artifacts/qualification/V050_50K_OUTCOMES_R2.json \
  --limit 64 \
  --output /artifacts/qualification/V050_EDGE_QUERIES.json

uv run python scripts/profile_v050_edge.py \
  --sqlite-pack v050_10k=/artifacts/packs/simplewiki-v050-20260701-10k-final.sqlite \
  --sqlite-pack v050_50k=/artifacts/packs/simplewiki-v050-20260701-50k-final.sqlite \
  --queries /artifacts/qualification/V050_EDGE_QUERIES.json \
  --output /artifacts/qualification/V050_EDGE_PROFILE.json
```

`POSIX_FADV_DONTNEED` is advisory. The resulting cold-cache evidence records
both that limitation and the Linux storage-layer physical-read counter. Host
measurements and analytical projections are never labeled as board results.

## 5. Validate source and package contracts

```bash
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy --strict src
```

The final decision integrator refuses incomplete outcome matrices, benchmark
hash mismatches, unverified pack hashes, or missing progressive scales.
