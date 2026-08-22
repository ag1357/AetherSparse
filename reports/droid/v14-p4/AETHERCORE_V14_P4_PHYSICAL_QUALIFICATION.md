# AetherSparse V14 — ESP32-P4 Accessory Physical Qualification

**Branch:** `work/aethercore-v14-p4-physical-qualification`
**Classification:** **P4_RETAIN_STORAGE_UPGRADE**
**Date:** 2026-08-22 · **Host:** factory droid session · **Mission:** AetherSparse V14 (phases 0-11)

## Verdict

The frozen V14 edge candidate runs on the physical accessory ESP32-P4 with
**bit-exact logical behavior** — 51/51 frozen ABI vectors, then the witnessed
260-case replay reproduced all 1,329 policy decisions (per-candidate int8
scores verified) and all 107 address queries (candidate digests, entity sets,
occurrence totals) against the authenticated trace bundle. Nothing about the
deployment alters the V14 logical results.

The physical bottleneck is **removable-storage latency**, not compute, not
memory: an address query's wall time is ~98% page I/O on the vendor-qualified
20 MHz SDMMC path.

## Hardware under test

| Item | Value |
|---|---|
| Board | Waveshare ESP32-P4-WIFI6 (SKU 32020), *not* the Tactility appliance |
| Chip | ESP32-P4 **rev v1.3**, dual-core RISC-V @ 360 MHz (only production clock) |
| PSRAM | 32 MiB HEX @ 80 MHz (AP Memory) |
| Flash | 32 MB GD detected (16 MB image header; warning benign) |
| MAC | 80:f1:b2:d1:eb:6e |
| Card | USD00 128 GB microSD, FAT32 `AETHERCORE`, SDMMC 4-bit @ **20 MHz** (vendor-qualified ceiling on rev v1.3) |

## Phase results

| Phase | Result | Headline |
|---|---|---|
| 0 pre-flight | PASS | exact V14 branch, 452 host tests, ESP-IDF v5.5.1 isolated |
| 1 real build | PASS | image 393 KB; runtime 11,538 B flash; policy 1,292 B bound |
| 2 on-device parity | PASS | **51/51** frozen vectors on hardware (`phase2-parity.json`) |
| 3 edge pack | PASS | 4 regions, 1.15 GB, `acpack:0d0a7702…`, sha256-verified host-side |
| 4 storage | PASS | seq read **1.93 MB/s**, rand 4 KiB **p50 36.6 ms / 24 IOPS**, write 0.93 MB/s |
| 5 cache ladder | PASS | address p50 cold: zero 2,481 ms · 256 KiB 1,037 ms · **1 MiB 1,086 ms** · 2 MiB 831 ms |
| 6 trace replay | PASS | **LOGICAL_PASS**: 260/260 cases, 1,329/1,329 decisions, 107/107 queries, 1,543,864 MACs |
| 7 regressions | PASS | 260/260 across all 6 tier×partition cells; 18/18 expected failures reproduced; zero unsupported answers |
| 8 memory | PASS | resident **2.06 MB** actual vs **2.80 MB** predicted (74%) |
| 9 CPU/latency | PASS | 360 MHz measured; 200/300/400 interpolated (unsupported clocks) |
| 10 attribution | PASS | storage wait ≈ **98%** of address wall; evidence-directory probes = 62% of misses at 1 MiB |
| 11 classification | **P4_RETAIN_STORAGE_UPGRADE** | see rationale |

On-device integrity: all four pack regions re-hashed on the deployment path
(sha256, streamed) — **all match** the host-verified card inventory
(604.8 s, software SHA at 360 MHz ≈ 1.9 MB/s).

## Prediction vs actual (key scalars)

| Metric | Reference (analytical) | Physical (this board) | Ratio |
|---|---|---|---|
| Address p50 @300 MHz ref | 63.65 ms | 1,086.5 ms (360 MHz, 1 MiB, cold) | **17.1×** |
| Address p95 @300 MHz ref | 129.94 ms | 2,265.7 ms | 17.4× |
| Resident @1 MiB cache | 2,797,320 B | 2,061,221 B | 0.74× |
| Storage seq read | (host reader 35.5 MB/s, non-authoritative) | 1.93 MB/s | 18.4× slower |
| Storage random 4 KiB | (host reader 674 IOPS) | 24.0 IOPS | 28× slower |
| Policy decision CPU | — | p50 638 µs, p95 6,132 µs @360 MHz | — |

The address path does **not** scale with CPU clock: at 20 MHz SDMMC with this
card, page-read latency dominates (p50 ~37 ms random 4 KiB). The analytical
references implicitly assumed a far faster page-addressable medium.

Cache ladder (107 witnessed surfaces, cold pass):

| Cache | p50 ms | Hit rate | Misses | Evidence-dir share of misses |
|---|---|---|---|---|
| 0 (control) | 2,481 | 0.000 | 111,685 | 84.0% |
| 256 KiB | 1,037 | 0.660 | 37,969 | 62.3% |
| 1 MiB (reference) | 1,086 | 0.663 | 37,636 | 61.9% |
| 2 MiB | 831 | 0.758 | 26,996 | 48.8% |

## Bottleneck attribution (phase 10)

- Replay wall 138.3 s; policy CPU total 2.63 s (**1.9%** compute) →
  storage wait fraction ≈ **0.98**.
- The **evidence directory** (4.4 MB flat sorted array, binary search ≈ 18
  paged probes per entity lookup) is the single largest avoidable-miss source:
  62% of misses at 1 MiB cache. A two-level paged B-tree (~2 probes) or a
  resident directory (it fits PSRAM trivially) removes most of these; that is
  a layout decision for the next architecture revision, not a media fix.
- Cold vs warm barely differ at 1 MiB (1,086 vs 1,030 ms p50): per-query
  working sets are largely unique; inter-query reuse is low at these sizes.
- Controls run: zero-cache (hits exactly 0), cold/warm at every ladder size,
  host USB-reader baseline (explicitly non-authoritative).

## Classification rationale (phase 11)

**P4_RETAIN_STORAGE_UPGRADE.**
Compute is adequate (bit-exact policy at 638 µs p50; < 2% of wall). Memory is
ample (2.06 MB resident of 32 MiB PSRAM; under the 2.80 MB prediction). The
dominant, unavoidable cost is removable-storage latency: even a perfect cache
leaves every query paying ~14 K pages/query of postings+surface traffic at
~37 ms-class random reads. Not `P4_COMPUTE_LIMITED` (CPU fraction negligible),
not `P4_MEMORY_LIMITED` (resident far under envelope), not
`P4_RETAIN_CACHE_OPTIMIZE` alone (2 MiB helps but the media floor remains the
ceiling), not `P4_DEPLOYMENT_BLOCKED` (every phase completed physically).

**Power:** UNMEASURED (no measurement hardware available; per mission, not
invented).

## Deployment fixes required (all generic, committed)

1. TF slot power: on-chip **LDO channel 4** power-control handle (the card
   never answers OCR without it; matches the vendor `09_sdmmc` example).
2. SDMMC **20 MHz first** (40 MHz not qualified on rev v1.3; a failed attempt
   wedges the slot driver — full deinit between attempts).
3. `CONFIG_FATFS_FS_LOCK=0` — the FatFs lock pool capped concurrent opens at
   two, failing the third region open with ENFILE.
4. Main task stack **16 KB** (the 12 KB build completed with a 140 B
   high-water margin during replay).
5. `embed_policy.py`: bind the ABI-canonical identity (schema 14, model
   `0x987D28FC667044BE`) instead of placeholders; header regenerated.

## Reproduction

```bash
# host
git checkout work/aethercore-v14-p4-physical-qualification
PYTHONPATH=src python3 -m pytest tests                 # 452 pass
PYTHONPATH=src python3 scripts/droid/v14_p4_trace_export.py \
  --bundle <replay-3tier> --pack <aethercore-knowledge> --output <out>
# firmware (ESP-IDF v5.5.1)
cd firmware/p4_qualification && idf.py build flash
# card: active-packs.json + aethercore-knowledge/ + aethercore-traces/
# run: boot log emits single-line MEAS JSON; parse with any JSON line reader
```

Logs: `phase2-boot.log`, `phase4-boot.log`, `phase5-8-boot.log` (one
continuous 45-minute run containing verify + phases 4-8).
Data: `phase*-device.json`, `prediction-vs-actual.json`,
`aethercore-v14-p4-physical-qualification.json`.
