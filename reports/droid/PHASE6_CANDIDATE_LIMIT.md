# Phase 6 — Candidate Budget Sweep

Branch: `droid/retrieval-accuracy-v06`
Pack: `selector-10k-p3.sqlite` (unchanged)
Benchmark: frozen V050 R1 (hash-verified)
Prior state (Phase 5, commit `159c3e8`): fusion strict 75.62% / lenient 83.52%
All sweep runs include the kept Phase 5 state (discourse boost 0.35 + carry injection).

## Sweep table (fit partitions: tuning+development, 424 ANSWER cases)

| candidate_limit | strict | lenient | candgen p50 (ms) | candgen p95 (ms) | select p95 (ms) |
|---|---|---|---|---|---|
| 48 | 0.7571 | 0.8208 | 400.6 | 3821.7 | 0.46 |
| 64 (prior default) | 0.7571 | 0.8208 | 401.4 | 3635.5 | 0.47 |
| **96 (chosen)** | **0.7594** | **0.8231** | 364.5 | 3365.2 | 0.43 |
| 128 | 0.7594 | 0.8231 | 412.3 | 3639.8 | 0.52 |

The curve is flat: 48 and 64 are identical; 96 and 128 are identical, one fit
case ahead. Latency is dominated by FTS I/O on the USB-hosted pack and does not
move with the limit (select overhead stays <1 ms — ranking cost is not the
constraint). The mission's worry that "the candidate window may be costing real
accuracy" does not hold on this stack: the bounded pool sources (main FTS 48,
per-entity decomposition, expansion probe 12, carry probe 3, link supplemental)
already cover what the window would add.

**Knee chosen: 96** — captures the only recall gain; 128 adds nothing but pool
size. The extra headroom also matters for the full 397k-doc pack, where FTS
returns more hits per query and the cap binds more often.

## Full measurement at candidate_limit=96 (all 1,280 ANSWER cases)

fusion: **strict 75.62% -> 75.70% (+0.08)**, lenient 83.52% -> 83.59% (+0.08).
Exactly the one case the fit-partition sweep predicted. No category regressed.

Selector default `candidate_limit` changed 64 -> 96; tests pass (176/176).

Artifacts: `phase6/sweep-{48,64,96,128}.json`, `phase6/full-96-10k.json`.
