# Phase 5 — Discourse Carry-Over

Branch: `droid/retrieval-accuracy-v06`
Pack: `selector-10k-p3.sqlite` (unchanged)
Benchmark: frozen V050 R1 (hash-verified), 1,280 ANSWER cases
Prior state (Phase 4, commit `630fc96`): fusion strict 75.31% / lenient 83.20%

## What the mission asked
Carry the previous turn's resolved document forward as a **soft boost** on the
next turn's candidates — not by concatenating text. Boost tuned on tuning only.

## What the measurements forced into the design

1. **Boost-only is a no-op (measured).** Applying an additive boost
   (0.05–0.50, tuning partition) to candidates from the parent turn's predicted
   top-1 document changed exactly zero outcomes: the failing follow-up questions
   ("And how does that source define Billion?") never had the parent document in
   the candidate pool, so there was nothing to boost. A soft boost can only act
   on candidates that exist.
2. **Carry injection makes the boost real.** `candidates()` gained an optional
   `carry_document_id`: a bounded probe (FTS within the carried document on query
   content terms, top 3; lead-chunk fallback) adds the carried document's chunks
   to the pool. `select()` gained `discourse_document_id` / `discourse_boost`
   kwargs; the boost is applied at ranking time in the fusion, reranker, and
   targeted-traversal orderings. The carried document is the parent turn's
   predicted top-1 at the final (reranker) stage — the system's own previous
   answer, never gold. Cases without parents are untouched by construction.

## Boost tuning (tuning partition only)
Sweep {0.05, 0.15, 0.25, 0.35, 0.50, 0.75, 1.0} with injection active: flat —
strict 77.91% at every value, follow_up/pronoun/direct_fact all unchanged, no
value harmful even at 1.0. With tuning indifferent, chose **0.35** (the mission
testbed's reference value, mid-range). Ablating boost on top of injection over
all 200 with-parent cases: fixed=0, broken=0 — the injection is the effective
component; the boost is retained as ordering insurance at zero measured cost.

## Measurement (10k pack, all 1,280 ANSWER cases, boost 0.35 + injection)

| category | strict before | strict after | delta |
|---|---|---|---|
| follow_up | 0.9600 | 0.9800 | +0.0200 |
| pronoun | 0.9600 | 0.9800 | +0.0200 |
| direct_fact | 0.9818 | 0.9818 | 0.0000 |
| all other categories | — | — | 0.0000 (exactly) |

Overall fusion: **strict 75.31% -> 75.62% (+0.31)**, lenient 83.20% -> 83.52% (+0.31).
Held-out (informational): evaluation +0.15, final_held +1.52.

## Gate
Mission gate "follow_up and pronoun recall improve without degrading
direct_fact": **PASS** (+2.0 each; direct_fact exactly unchanged).

Artifacts: `phase5/discourse-0.35-10k.json` (boost-only, flat — the negative
result), `phase5/discourse-injection-0.35-10k.json` (kept state).
