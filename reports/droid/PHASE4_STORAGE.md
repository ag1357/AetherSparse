# Phase 4 — P4 storage software tuning: microSD is already at its floor

Date: 2026-08-04. Hardware: Waveshare ESP32-P4-WIFI6, SD08G 8 GB microSD,
LDO ch4 power, SDMMC. Bench app: `work/esp/p4_sdmmc_bench/` (new `REMOUNT
<khz>` and `RAWBENCH <nsectors>` commands; driver `work/esp/phase4_tune.py`,
log `work/esp/p4_phase4.log`). All numbers: raw-sector random reads, n=1200,
post-reboot reproduction of the Mission 1 baseline.

## Verification of the shipped config (mission items 1-2)

| item | finding |
|---|---|
| Slot | **SLOT_1** (runtime-printed `slot=1`) — the 4-bit GPIO-matrix slot; SLOT_0's 8-bit path is UHS-I-dedicated and driver-unsupported, so SLOT_1 is the only usable slot for the TF cage |
| Bus width | **4-bit** (`width=4`, pins CLK=43 CMD=44 D0-D3=39-42) |
| Clock | **40 MHz already** (`freq_khz=40000`, `real_khz=40000`, card max 40000) — not defaulting to 20 MHz |
| CMD18 | **already in use** — `sdmmc_read_sectors(..., 8)` issues one multi-block read per 4 KB |
| `dma_aligned_buffer` | **not applicable** — the field is documented "Leave it NULL. Reserved for cache aligned buffers for SDIO mode"; the SD-memory transaction path in this IDF (5.4) performs **no per-transfer heap allocation** (verified in `sdmmc_host.c`/`sdmmc_cmd.c`) |

## Experiments (random 4K p50/p95/p99 µs, n=1200)

| config | p50 | p95 | p99 |
|---|---|---|---|
| baseline 40 MHz 4-bit (repro) | 1405 | 1781 | 1869 |
| 20 MHz clock | 1544 | 1903 | 1986 |
| 40 MHz, 512 B read (1 sector) | 1183 | 1445 | 1481 |
| 40 MHz, 4 KB (8 sec) | 1401 | 1752 | 1875 |
| 40 MHz, 8 KB (16 sec) | 1692 | 2016 | 2136 |
| 40 MHz, 16 KB (32 sec) | 2125 | 2454 | 2602 |
| 40 MHz, 32 KB (64 sec) | 2990 | 3312 | 3439 |
| 40 MHz, 64 KB (128 sec) | 4705 | 5035 | 5156 |

Baseline reproduction vs Mission 1: 1405/1781/1869 vs 1401/1782/1888 µs —
within noise, post-reboot.

## Analysis

- **Clock:** 20→40 MHz is worth only −139 µs (−10%), not 2×. The bus
  transfer component of a 4K read is ~218 µs (1401−1183), matching the
  40 MHz/4-bit theoretical 205 µs — the bus is already saturated efficiently.
- **CMD18 amortization:** fixed per-command cost ≈ 1183 µs (1-sector p50);
  each additional 4 KB adds ~218 µs. Per-command driver overhead is NOT the
  bottleneck; larger multi-block reads amortize exactly as expected
  (4×4K separately = 5.6 ms vs one 16K read = 2.1 ms — 2.6× per-byte better;
  this is the Phase 5 design input).
- **The floor is the card:** ~1.18 ms of the 1401 µs p50 is the SD08G's
  internal random-access latency. No software knob in this stack reaches it.

## Gate

**Best-achievable microSD random 4K p50 = 1401 µs** (the shipped config was
already optimal; tuning recovered nothing because nothing was misconfigured).

1401 µs > 800 µs → **eMMC is justified by the mission's rule.** Expected gain,
stated explicitly for the purchase decision:

- Bus width ~2×: **NOT available** — the Waveshare TF interface wires only
  D0-D3 (4 lines); an 8-line eMMC would need separate wiring to SLOT_0 pins,
  which this bench cannot verify. Do not count bus-width gains.
- Controller/card random latency ~3-5×: applies. Expected eMMC random 4K p50
  ≈ 1183/3.5 + 218 ≈ **340-610 µs** (latency component 3-5× better, transfer
  unchanged at 4-bit 40 MHz) — under the 800 µs line.

Purchase recommendation: justified on controller latency alone, expected
~2.3-4.1× random-read improvement, contingent on wiring eMMC to a 4-bit-capable
header (SLOT_1 pins) — 8-bit wiring would add the bus-width gain but is
unverified on this carrier.
