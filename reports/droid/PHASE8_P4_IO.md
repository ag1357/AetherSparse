# Phase 8 — ESP32-P4 SDMMC cold-cache I/O characterization

Date: 2026-08-03. Mission phase 8: measure whether an ESP32-P4 serving an
AetherSparse corpus pack from a microSD card can answer retrieval queries at
interactive latency, with no OS page cache (true cold cache).

## Hardware / setup

| Item | Value |
|---|---|
| Board | Waveshare ESP32-P4-WIFI6 (ESP32-C6 radio addon) |
| SoC | ESP32-P4 rev v1.3, 360 MHz, 32 MB PSRAM (hex, 20 MHz) |
| Card | SD08G 8 GB SDHC (FAT32, 4 KB clusters), re-seated before the SDMMC runs |
| SD bus | SDMMC slot, 40 MHz, 4-bit; card power via on-chip LDO channel 4 (3.3 V) |
| ESP-IDF | v5.4.2; console UART 1.5 Mbaud (CH343 USB-serial) |
| Bench app | `work/esp/p4_sdmmc_bench/` (outside the repo): `main.c`, `sqlite_vfs.c`, `sqlite3.c` (SQLite 3.53.4 amalgamation, SHA3-256 verified against sqlite.org) |
| Host driver | `work/esp/host_bench.py` (pyserial, resumable base64 PUTBIN) |

The ESP32-P4 has no OS page cache for FAT files: every read reaches the card
through the SDMMC driver. That is the cold cache this phase measures.

## Results

### Random 4 KB read latency, raw block reads (`sdmmc_read_sectors`, read-only)

| Bus | n | mean | p50 | p95 | p99 | min | max |
|---|---|---|---|---|---|---|---|
| SDMMC 40 MHz 4-bit | 1200 | 1397 µs | **1401 µs** | **1782 µs** | **1888 µs** | 447 µs | 2017 µs |
| SDMMC (repeat run) | 1200 | 1398 µs | 1404 µs | 1788 µs | 1885 µs | 446 µs | 2028 µs |
| SDSPI 20 MHz | 1200 | 3922 µs | 3930 µs | 4264 µs | 4308 µs | 3045 µs | 4317 µs |

Uniform random offsets across the whole card, 8 sectors per read, 1200 samples
(mission requirement ≥1000). Two SDMMC runs reproduced within 1%.

### Sequential read throughput (64 MB)

| Bus | Throughput |
|---|---|
| SDMMC raw | **14.72 MB/s** |
| SDSPI raw | 1.63 MB/s |

### On-device FTS5 query, cold cache (the mission query)

Production-shaped query (`store.search()` top-term OR with bm25 ranking,
`LIMIT 48`) against the real `selector-1k-p3` pack (59,813,888 bytes, 1000
documents, page_size 4096) copied to the card and verified byte-exact:

```
RESULT fts_extent rc=0 clusters=14603 csize=8 contig=1 size=59813888
RESULT fts_query n=20 mean_us=393399 p50_us=392907 p95_us=405346 p99_us=405346
                 min_us=391860 max_us=405346
```

- **p95 wall-clock: 405 ms** (p50 393 ms), 20 iterations, all `rc=0`, all
  returning the same 48 rows as the host-side reference.
- Cold-cache protocol: SQLite page cache held at 16 pages (64 KB) and
  `sqlite3_db_release_memory()` between iterations; every query re-reads its
  pages from the card. ~393 ms ≈ 280 page reads × 1.4 ms raw p50 — internally
  consistent with the random-read benchmark.
- SQLite ran with its heap in 8 MB of PSRAM (`SQLITE_CONFIG_HEAP` +
  `SQLITE_ENABLE_MEMSYS5`); reads served by a custom `rawsd` VFS that resolves
  the pack's FAT cluster chain once at open and then issues raw
  `sdmmc_read_sectors` calls (the pack is contiguous on the freshly formatted
  card, `contig=1`).

### FatFs-level numbers (caveat, not the deliverable)

| Bus | random 4K p50 | sequential |
|---|---|---|
| SDMMC stdio/fseek | 99.5 ms | 0.88 MB/s |
| SDSPI stdio/fseek | 162.5 ms | 0.44 MB/s |

These are **FAT cluster-chain-walk artifacts**, not card limits: FatFs
`fseek` walks the cluster chain from the start on every seek, so a random
page read through stdio costs O(offset) FAT walking (~100 ms mid-file on this
card). Any on-device query engine must avoid stdio `fseek` on large files —
use a contiguous file plus raw sector reads (as the `rawsd` VFS does), or a
raw partition.

## Engineering notes (reproduce/resume)

- The Waveshare board powers the TF slot from on-chip LDO channel 4; the card
  does not enumerate without `sd_pwr_ctrl_new_on_chip_ldo(4)` +
  `host.pwr_ctrl_handle` (vendor example `09_sdmmc` in ESP32-P4-Platform).
- Once a card is initialized in SPI mode it latches until power-cycled; the
  LDO rail survives chip resets, so SDMMC-after-SDSPI needs a physical reseat.
- `SQLITE_CONFIG_HEAP` is compiled out unless the amalgamation is built with
  `-DSQLITE_ENABLE_MEMSYS5` (silent `SQLITE_ERROR` → `SQLITE_NOMEM` at open).
- The packs are WAL-mode; building with `-DSQLITE_OMIT_WAL` makes SQLite
  reject them (`SQLITE_NOTADB`, rc=26). WAL support must stay in.
- SQLite + FatFs + the bench app overflow the default 3584-byte main task
  stack (stack protection fault); `CONFIG_ESP_MAIN_TASK_STACK_SIZE=16384`.
- Pack transfer over the cooked-mode console: base64 lines (8000 chars) with
  per-line ACKs, 64 KB buffered card writes, resumable via `HAVE`/`PUTBIN
  <size> <offset>`; 59.8 MB in ~31 min at ~32 KB/s (console polled-RX bound).

## Conclusion

The ESP32-P4 + commodity microSD serves cold 4 KB reads at **1.4 ms p50 /
1.9 ms p99** and 14.7 MB/s sequential over SDMMC — and a representative
production FTS5 query against a real 60 MB pack completes cold in
**~0.4 s (p95 405 ms)**. On-device pack retrieval is viable on this hardware
provided reads bypass FatFs seeking (raw sectors over a contiguous extent);
SDSPI costs ~2.8× random latency and ~9× sequential throughput.
