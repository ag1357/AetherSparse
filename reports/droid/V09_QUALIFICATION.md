# V09 Qualification — Mission 4: Controller-First Remediation

Branch `droid/controller-v09`. Benchmark INDEPENDENT_NATURAL_QUERY_SET_V050_R1
(sha256 `1e8b89427898df3c…`, verified identical in both evaluation locations).
Product metric: **mode-3 canonical value accuracy** (oracle-free, canonical
form equality with date-granularity containment). All controller changes
dual-measured in mode 2 (oracle evidence, diagnostic) and mode 3 (product).

## 1. Headline: mode-3 canonical scaling curve

| tier | mode-3 canonical | strict article recall |
|---|---|---|
| 10k | **37.34%** | 83.05% |
| 25k | PENDING-BATTERY | PENDING-BATTERY |
| 100k | PENDING-BATTERY | PENDING-BATTERY |
| 397k | PENDING-BATTERY | PENDING-BATTERY |

Mode-3 is the product number; strict article recall is secondary (retrieval
diagnostic). Battery: sharded ×8 serial harness, mode-3 product conditions,
Amendment A traced, Lane C sidecar + Lane D compat + Phase 3/4 active
(`battery9.service` on s600, 2026-08-11).

## 2. Transfer rates — the methodological result of the mission

| change | mode-2 delta | mode-3 delta | transfer |
|---|---|---|---|
| Phase 3 (3.1+3.2, disposition + tiebreaks) | +13.98 pp | +0.4 pp | **~3%** |
| Phase 4.1 (LIST slot-shape binding) | +3.36 pp | +3.36 pp | **100%** |
| Phase 4.2 (comparison value-kind pairing) | +0.47 pp | +0.31 pp | **66%** |

Phase 3's gains were real in mode 2 but invisible to the product (~3%
transfer): they acted on behavior that only oracle evidence reaches. Phase 4
operators were built only after the branch check showed mode-3 retrieval
already delivers all gold documents for 76.9% of composition failures — so
the gains transfer. **Build against the evidence the product actually has;
report mode-3 first.** The 20% transfer floor is a standing stop-and-reassess
signal (GATE_DECISIONS.md).

## 3. Gate records

- Phase 3: SHORT-ACCEPTED +13.98 pp mode-2 canonical (reports/droid/v09/PHASE3_GATE.md).
- Lane C (misspelling ed≤2 sidecar): SHORT-ACCEPTED +4.00 pp at 10k/25k/100k,
  zero regressions; wrong-correction cliff (−12 to −38 pp) recorded as the
  decisive negative (GATE_DECISIONS.md).
- Lane D (carry): shipped compat as-is on a fresh paired 397k baseline
  (stale-baseline artifact debunked; standing paired-baseline rule).
- Phase 4 (composition): gate +10 pp of class; delivered **+20.6 pp**
  (238→189 residual) via 4.1 (+43 cases) and 4.2 (+6 cases)
  (reports/droid/v09/PHASE4_GATE.md).

## 4. Compile-time residual line item (composition class)

Residual composition failures under shipped Phase 4 code (live mode-3,
no cache): **197** of the original 238 (41 fixed live; the cache-replay
measurement fixed 49 — the 8-case delta is cache-vs-live replay noise).

Classified with oracle evidence (retrieval removed as a cause; artifact:
reports/droid/v09/phase4-residual-compile-time.json):

| origin | cases | share |
|---|---|---|
| selection-residual (gold claims extractable, not selected/composed) | 155 | 78.7% |
| **mangled-value (compile-time)** — e.g. `20128,%` vs gold `01.0162%` | 19 | 9.6% |
| **absent-claim (compile-time)** — gold value never extracted | 21 | 10.7% |
| mixed (one part mangled, one absent) | 2 | 1.0% |
| **compile-time subtotal** | **42** | **21.3%** |

Reading: extraction-mangled values and absent claims are knowledge-compiler
defects — 42 cases (21.3% of the composition residual, 3.3% of all answer
cases) are unreachable by any controller or retrieval work. This is the
first direct measurement of the compile-time floor on product accuracy.
The larger share of the composition residual (155 cases) remains
selection/evidence-side: gold values extract, but mode-3 evidence delivery
(~23% of the class per the Phase 4 branch check) or per-slot selection
still fails. Compile-time extraction quality is now a measured limiter and
a candidate to outrank retrieval work in the next mission; it is not yet
the majority of the composition residual.

## 5. Amendment A (trace/metadata only, metric-neutral)

- A1 operator registry, A2 per-op records, A3 all attempts retained,
  A4 block_reads in 4 KB units, A5 JSONL keyed (tier, config hash,
  benchmark, commit) with evaluation/final_held training_eligible=false.
- A6 trace corpus:
  - **10k**: full 2050 cases, product conditions, `trace-10k-full.jsonl`
    (sha256 `d451415dc9891740…`; reproduced the untraced mode-3 numbers
    exactly: exact 36.80%, disposition 75.12%).
  - **25k/100k/397k**: sharded ×8 traces produced by the Phase 9 battery
    (full 2050-case coverage at every tier, exceeding the 400-case
    stratified fallback). Rationale (user directive): tool-selection
    behaviour at 397k differs materially from 10k — candidate absence is
    far higher — and a policy trained only on 10k traces would learn
    decision rules from a regime where retrieval rarely fails.
- A6 stats block: PENDING.

## 6. Schema reservations (schema and documentation only, no capability)

Landed in src/aethersparse/controller/models.py with contract tests
(tests/controller/test_models.py::TestSchemaReservations, green):

1. Entity-ID bands: `CORPUS_ENTITY_ID_PREFIX` reserved for corpus entities;
   a USER band is reserved for conversation entities.
2. `ExactSourceSpan.source_class`: CORPUS | CONVERSATION (default CORPUS).
3. `StructuredClaim.grounding`: CORPUS_GROUNDED | USER_ASSERTED (default
   CORPUS_GROUNDED); USER_ASSERTED claims documented as ineligible to
   ground answers once conversation sources exist.

## 7. Integrity

- Benchmark sha256 `1e8b89427898df3c…` identical on Pi and s600.
- 10k pack sha256 `aef284ff0f157d2d…` (restored byte-identical from s600
  after the exFAT truncation incident; verified via cache-replay provenance).
- Trace corpus replicated to s600 (`/root/work/v08/battery9/`).
- Pack set on s600: 10k / 25k / 100k / full(397k) + ed2 sidecars for
  25k/100k/397k (10k sidecar local).

## 8. Deferred, not dismissed

- Phase 5/6 (candidate generation ~1.5 pp of product; alias@100k candidate
  absence 38.6% — real scale defect, deferred).
- Wrong-correction cliff (Lane C v1–v4).
- Residual composition classes: list:missing_parts / no_realization
  (claim absence), comparison value extraction noise.
