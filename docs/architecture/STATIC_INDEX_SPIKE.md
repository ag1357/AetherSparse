# Static Index Feasibility Spike (Phase 5, design only — not implemented)

Date: 2026-08-04. Timebox: 2 h. Status: **spike document; no code written.**

## Motivation (measured, not hypothetical)

Mission 1 Phase 8 and Mission 2 Phase 4 established on the Waveshare
ESP32-P4-WIFI6 + SD08G microSD:

- FatFs cluster-chain walking on large files is pathological (~100 ms/page
  mid-file) — raw sector reads are already required (rawsd VFS).
- Raw random 4K read: p50 1401 µs / p95 1781 µs; fixed per-command cost
  ~1183 µs; marginal transfer ~218 µs per 4 KB (40 MHz/4-bit, bus-saturated).
- A 16 KB multi-block read costs 2125 µs — 2.6× cheaper per byte than four
  4K reads. **Large aligned reads are the lever.**
- On-device SQLite FTS5 query: p50 392.9 ms / p95 405.3 ms (n=20, cold),
  implying ~200-300 random page reads per query (B-tree descent + scattered
  posting lists per term).

The ranking stack is now lexical-generation-bound (Phase 2/3 finding): the
candidate pool decides the recall ceiling. A static index attacks exactly
this layer.

## Design

### Layout (one contiguous file per pack, built host-side)

```
[manifest: 4K]  magic, version, term count, chunk count, FST offset/size,
                postings offset, block size, checksums
[FST dictionary]  minimal perfect FST, term -> (posting offset, length,
                max-score).  Resident in PSRAM at boot.
[posting blocks]  4 KB-aligned, impact-ordered.  Each block:
                header {block_max_score, last_chunkid_delta_base, count}
                payload: delta+varint chunkids || varint quantized scores
```

### Measured size inputs (10k pack)

- 147,549 chunks, avg 275 chars (~45 tokens/chunk)
- ~74k distinct terms in a 10% sample → ~200k terms at 10k (Heaps β≈0.45)
- aliases: 16,079 (mission quoted 12,524 at an earlier build; either way
  « 1 MB)
- Full 397k corpus (measured from `selector-full-p3.sqlite`): **2,799,975
  chunks**, 7.6M links, 224,834 anchor aliases; ~1.0-1.5M terms projected

### Component budgets (full corpus)

| component | size | where |
|---|---|---|
| FST dictionary (~1.2M terms) | 6-15 MB | PSRAM (32 MB) — fits; aliases FST « 1 MB alongside |
| postings (~125M entries, 1-2 B id delta + 1 B score) | 0.3-0.5 GB | SD, contiguous, 4K-aligned |
| block-max metadata | inside block headers | — |

### Query path (on-device)

1. Tokenize query; FST lookups in PSRAM — **zero I/O** per term.
2. Per term, fetch posting blocks in impact order; BlockMax WAND with early
   termination once the top-96 candidate set cannot be displaced.
3. Expected reads per query: 4-6 terms × 1-3 blocks (4-16 KB) ≈ **6-12
   block reads** vs the current ~200-300.

### Latency estimate (P4, current microSD)

- I/O: 10 reads × ~2.1 ms (16K-block cost) ≈ 21 ms typical; worst-case
  deep lists bounded by early termination (~25 reads) ≈ 53 ms.
- CPU: varint decode + score accumulation ~50-100k postings ≈ 5-15 ms
  (360 MHz, ~100 ns/posting).
- **Estimated query p50 ≈ 25-35 ms, p95 ≈ 50-70 ms** — an ~8-10× improvement
  over the measured FTS5 p95 of 405 ms, and ~25× fewer random reads.
- With the Phase 4 eMMC option (340-610 µs random 4K): p95 ≈ 15-25 ms.

### What this buys the retrieval stack

Candidate generation stops depending on SQLite FTS5 page walks; the same
rawsd sector driver serves the index. The Phase 3 lesson (pool presence is
the recall ceiling) means deeper, cheaper posting reads directly raise the
ceiling at full-corpus scale.

## Engineering cost (if promoted to Mission 3)

| piece | estimate |
|---|---|
| host-side builder (postings sort, impact order, block layout, FST build, manifest) | 2-3 days |
| on-device reader (block fetch over existing rawsd path, varint decode, BlockMax WAND, top-k heap) | 3-5 days C |
| integration (selector candidate source), pack format versioning, tests | 2-3 days |
| **total** | **~1.5-2 weeks** |

## Risks

- PSRAM headroom at full corpus (FST 6-15 MB of 32 MB) — acceptable, must be
  re-verified if the lexicon exceeds ~2M terms.
- Static = rebuild-on-corpus-change; acceptable for the product's pack model.
- Impact-ordering quality depends on build-time static scores (BM25 upper
  bounds); a mis-scored build degrades recall silently — needs a host-side
  recall regression gate against the current FTS5 path before shipping.

## Recommendation

The numbers justify promotion: ~25× read-count reduction and ~8-10× on-device
query latency for ~1.5-2 weeks of work, using only the already-proven raw
sector path. **Recommend scheduling as Mission 3.**
