# AetherSparse / AetherCore

An edge-oriented **grounded cognitive system**: a tiny learned controller that
operates an explicit cognitive state over provenance-bound knowledge, with every
answer produced through exact typed operations and an exact verifier. Fail-closed
by construction — unsupported answers are structurally impossible to emit.

**Current qualified state: V14, physically qualified on an ESP32-P4 accessory.**

```
natural / external input
        │
        ▼
input / state interpretation (NATURAL_LANGUAGE, STRUCTURED_EXTERNAL_EVENT)
        │
        ▼
Semantic Address v2 ──────────► provenance-bound external knowledge
        │                         (397,196 docs · 275,989 entities ·
        │                          1,334,801 exact surfaces, pinned corpora)
        ▼
Cognitive Obligation Graph (COG)
  goals · obligations · invariants · hypotheses · evidence ·
  unresolved state · exploration frontier · observed state
        │
        ▼
V14 adaptive controller — 1,292 int8 parameters / 1,292 bytes
        │
        ▼
exact typed operations / specialists / sandboxed tool plane
        │
        ▼
exact verifier ──(fail closed)──► grounded realization
```

## Current qualification status

### V14 software qualification (host, authenticated cohorts)

| Measure | Result |
|---|---:|
| Selected controller | 1,292 int8 parameters / 1,292 bytes, 34 exact operations |
| Autonomous reproduced-reachable | **242/260 (93.08%)** |
| Autonomous unseen tuning | **138/150 (92.00%)** |
| Wrong grounded selections | 18/260 |
| Invalid actions / verifier bypass / premature halt / runaway | **0 / 0 / 0 / 0** |
| HLE-style structural stress set | 9/9 — **structural qualification only; not a Humanity's Last Exam benchmark claim** |
| Host test suite | **452/452** |

### ESP32-P4 physical qualification (accessory device)

| Measure | Result |
|---|---:|
| Native frozen ABI vectors on hardware | **51/51 exact** |
| Witnessed-case replay on hardware | **260/260** |
| Controller decisions on hardware | **1,329/1,329 exact** |
| Address queries on hardware | **107/107 exact** |
| Unsupported-answer regressions | **0** |
| On-device pack integrity | all 4 regions sha256-verified on the deployment path |
| Hardware classification | **P4_RETAIN_STORAGE_UPGRADE** |

## Physical hardware status

Two physically separate devices exist:

- **DEVICE A — Waveshare touchscreen / Tactility appliance.** UI, media, and
  interface only. No cognition, no knowledge, no qualification target.
- **DEVICE B — AetherCore accessory compute device.** The cognitive engine.
  Current physical target: **Waveshare ESP32-P4-WIFI6, SKU 32020**
  (ESP32-P4 rev v1.3, dual-core RISC-V @ 360 MHz, 32 MiB PSRAM, temporary
  128 GB microSD qualification medium on 4-bit SDMMC @ 20 MHz).

Measured on Device B:

| Metric | Physical value |
|---|---:|
| Resident runtime (reference 1 MiB cache config) | **~2.06 MB** (prediction 2.80 MB) |
| Policy decision CPU | **p50 ~638 µs @ 360 MHz** |
| Address query latency | **p50 ~1,086 ms · p95 ~2,266 ms** |
| Storage sequential read | ~1.93 MB/s |
| Storage random 4 KiB | ~24 IOPS · p50 ~36.6 ms |
| Storage wait fraction of address wall time | **~98%** |

**Current bottleneck:** the storage random-access path and the paged
evidence-directory layout — **not** model compute, **not** RAM.

**Current next engineering path:** evidence-directory/layout optimization
(the flat sorted directory costs ~18 paged binary-search probes per entity
lookup; 62% of cache misses at 1 MiB), plus faster storage/eMMC qualification.

Full analysis:
[`reports/droid/v14-p4/AETHERCORE_V14_P4_PHYSICAL_QUALIFICATION.md`](reports/droid/v14-p4/AETHERCORE_V14_P4_PHYSICAL_QUALIFICATION.md)
and [`reports/droid/v14-p4/prediction-vs-actual.json`](reports/droid/v14-p4/prediction-vs-actual.json).

## Major components

- **Structured provenance-bound external knowledge** — pinned, hash-inventoried
  corpora; deployment packs carry manifests and per-region hashes.
- **Semantic Address v2** — deterministic, zero-learned-parameter query-span
  character index over the canonical address substrate.
- **Cognitive Obligation Graph (COG)** — explicit state
  `C = (G, O, I, H, E, U, F, S)`: goals, obligations, invariants, hypotheses,
  evidence (append-only), unresolved state, exploration frontier, observed state.
- **V14 adaptive controller** — COG-derived typed legal-mask structured
  perceptron; fixed-point int8; contains no answer text, no learned world facts.
- **5C immutable root constraints** — nine root classes (invariants,
  capabilities, permissions, verifier integrity, resources, physical hard
  limits, self-modification, rollback, fail-closed). The controller cannot
  rewrite evidence, bypass the verifier, or self-integrate components.
- **Sparse cold/warm/hot specialist interface** — typed contracts with
  activation states, schemas, resource/latency cost, permissions, provenance.
- **Persistent conversation state** — bounded sessions with exact
  serialization (836 B wire).
- **Sandboxed development/tool plane** — bounded typed tool tasks; no
  automatic integration.
- **Portable allocation-free C++17 runtime** — `native/aethercore_runtime`.
- **Stable C ABI** — versioned structs, frozen vectors verified host↔device.
- **ESP32-P4 firmware target** — `firmware/p4_qualification`.
- **Paged removable knowledge storage** — 4 KiB-page LRU pager over
  SD/eMMC-class media with reference-exact accounting.

## Qualification reports (current chain)

| Version | Report |
|---|---|
| V12 Semantic Address v2, real corpus | [`reports/droid/v12/SEMANTIC_ADDRESS_V2_REAL_CORPUS_QUALIFICATION.md`](reports/droid/v12/SEMANTIC_ADDRESS_V2_REAL_CORPUS_QUALIFICATION.md) |
| V13 agent vertical slice | [`reports/droid/v13/AETHERCORE_AGENT_VERTICAL_SLICE_QUALIFICATION.md`](reports/droid/v13/AETHERCORE_AGENT_VERTICAL_SLICE_QUALIFICATION.md) |
| V14 COG adaptive controller | [`reports/droid/v14/AETHERCORE_COG_ADAPTIVE_CONTROLLER_QUALIFICATION.md`](reports/droid/v14/AETHERCORE_COG_ADAPTIVE_CONTROLLER_QUALIFICATION.md) |
| V14 ESP32-P4 physical | [`reports/droid/v14-p4/AETHERCORE_V14_P4_PHYSICAL_QUALIFICATION.md`](reports/droid/v14-p4/AETHERCORE_V14_P4_PHYSICAL_QUALIFICATION.md) |
| V14 P4 prediction vs actual | [`reports/droid/v14-p4/prediction-vs-actual.json`](reports/droid/v14-p4/prediction-vs-actual.json) |

Earlier qualification history (v0.1–v11, including the failed flat-index
real-corpus baseline that motivated the cell/address redesigns) is retained
under [`reports/`](reports/) and is not deleted.

## Reviewer entry points

- [`docs/CURRENT_ARCHITECTURE.md`](docs/CURRENT_ARCHITECTURE.md) — concise
  architecture overview with diagram.
- [`docs/REVIEW_PACKET.md`](docs/REVIEW_PACKET.md) — source map, report map,
  hardware specs, and profiling numbers on one page.

## Reproduce (host)

```bash
uv sync --frozen --extra dev
uv run pytest -q                      # 452 passed
uv run ruff check src tests
uv run mypy src                       # strict
uv run aethersparse cells smoke --check
uv run aethercore-server --help       # integrated V14 service (/v14/query)
```

Firmware (ESP-IDF v5.5.1, ESP32-P4):

```bash
cd firmware/p4_qualification
idf.py build flash monitor            # emits single-line MEAS JSON records
```

Historical v0.4-era emulator commands remain available in
`src/aethersparse/cli.py`; they are not the qualified path.

## License

See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
