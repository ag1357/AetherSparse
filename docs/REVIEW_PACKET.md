# Review Packet — AetherSparse / AetherCore V14 (physical-qualified)

Everything an external reviewer needs, without reconstructing V0.4→V14 history.

## Exact current pointers

| Item | Value |
|---|---|
| Qualified physical branch | `work/aethercore-v14-p4-physical-qualification` |
| Qualified physical HEAD | `56cf18f7a5e08d50746b57c0e7e41df054f4d970` |
| Publication branch | `work/aethersparse-publication-refresh` (docs-only on top) |
| Hardware classification | **P4_RETAIN_STORAGE_UPGRADE** |

## Source map (what to read first)

### Cognitive core (Python, authoritative reference semantics)

| Path | Contents |
|---|---|
| `src/aethersparse/addressing/` | Semantic Address v2 implementation |
| `src/aethersparse/cognitive/graph.py` | Cognitive Obligation Graph (COG) |
| `src/aethersparse/cognitive/interpreter.py` | natural-language / external-state interpretation |
| `src/aethersparse/cognitive/models.py` | typed cognitive representations |
| `src/aethersparse/controller/adaptive_policy.py` | selected V14 controller |
| `src/aethersparse/controller/address_fusion.py` | address/candidate fusion |
| `src/aethersparse/controller/claim_address.py` | claim addressing |
| `src/aethersparse/five_c.py` | 5C immutable root constraints |
| `src/aethersparse/specialist_contracts.py` | specialist ABI/contracts |
| `src/aethersparse/agent/vertical.py` | integrated agent path |
| `src/aethersparse/agent/server.py` | `aethercore-server` service (`/v14/query`) |

### Native runtime (portable, allocation-free)

| Path | Contents |
|---|---|
| `native/aethercore_runtime/include/aethercore_runtime.h` | stable C ABI (versioned structs) |
| `native/aethercore_runtime/src/aethercore_runtime.cpp` | C++17 runtime, no exceptions/RTTI |

### ESP32-P4 physical deployment

| Path | Contents |
|---|---|
| `firmware/p4_qualification/main/main.cpp` | boot report + frozen-vector parity runner |
| `firmware/p4_qualification/main/pack_io.cpp` | SDMMC mount, PSRAM LRU pager, pack verify, IDX/ENT/EVD readers |
| `firmware/p4_qualification/main/trace_runner.cpp` | storage bench, cache ladder, witnessed-case replay |
| `scripts/droid/v14_p4_trace_export.py` | fail-closed trace-bundle exporter (refuses to emit unless 242/18/1329 reproduces) |

## Qualification reports (authoritative)

| Report | Path |
|---|---|
| V12 Semantic Address v2, real corpus | `reports/droid/v12/SEMANTIC_ADDRESS_V2_REAL_CORPUS_QUALIFICATION.md` |
| V13 agent vertical slice | `reports/droid/v13/AETHERCORE_AGENT_VERTICAL_SLICE_QUALIFICATION.md` |
| V14 COG adaptive controller | `reports/droid/v14/AETHERCORE_COG_ADAPTIVE_CONTROLLER_QUALIFICATION.md` |
| V14 ESP32-P4 physical | `reports/droid/v14-p4/AETHERCORE_V14_P4_PHYSICAL_QUALIFICATION.md` |
| V14 P4 prediction vs actual | `reports/droid/v14-p4/prediction-vs-actual.json` |

## Hardware specs (Device B — accessory compute)

Waveshare ESP32-P4-WIFI6 (SKU 32020); ESP32-P4 rev v1.3; dual-core RISC-V @
360 MHz; 32 MiB HEX PSRAM @ 80 MHz; 32 MB flash; 128 GB microSD (temporary
qualification medium) on 4-bit SDMMC @ 20 MHz. Device A (touchscreen/Tactility
appliance) is UI/media only and out of scope for compute.

## Current profiling numbers

Software (host): controller 1,292 int8 params; autonomous reproduced-reachable
242/260 (93.08%); unseen tuning 138/150 (92.00%); 18/260 wrong-grounded;
0 invalid / 0 verifier bypass; 452/452 tests.

Physical (device): 51/51 ABI vectors; 260/260 cases; 1,329/1,329 decisions;
107/107 address queries; resident ~2.06 MB (pred. 2.80 MB); policy p50
638 µs @360 MHz; address p50 1,086 ms / p95 2,266 ms (1 MiB cache, cold);
storage 1.93 MB/s seq, ~24 IOPS random 4 KiB; storage wait ≈ 98% of address
wall. Bottleneck: storage random-access path / paged layout. Next:
evidence-directory layout optimization + faster storage/eMMC qualification.

## Validate it yourself

```bash
uv sync --frozen --extra dev
uv run pytest -q                        # 452 passed
uv run ruff check src tests
uv run mypy src                         # strict
uv run aethersparse cells smoke --check # byte-for-byte determinism gate
```

Firmware: `firmware/p4_qualification` (ESP-IDF v5.5.1), physical run logs in
`reports/droid/v14-p4/phase5-8-boot.log` (single-line MEAS JSON records).
