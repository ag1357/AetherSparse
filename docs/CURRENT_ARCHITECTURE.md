# AetherCore — Current Architecture (V14, physically qualified)

Audience: reviewers who want the system as it is now, not the V0.4→V14 history.
Branch of record: `work/aethercore-v14-p4-physical-qualification` @ `56cf18f`.
Qualification report: `reports/droid/v14-p4/AETHERCORE_V14_P4_PHYSICAL_QUALIFICATION.md`.

## Dataflow

```
        natural language / structured external event
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Interpreter (cognitive/interpreter.py)                  │
│ NATURAL_LANGUAGE, STRUCTURED_EXTERNAL_EVENT             │
└──────────────┬──────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────┐
│ Cognitive Obligation Graph (COG)             │
│ C = (G,O,I,H,E,U,F,S): goals, obligations,   │
│ invariants, hypotheses, evidence (append-    │
│ only), unresolved, frontier, observed state  │
│ compact controller view: 19×u16 (38 B)       │
└───┬───────────────────────────────────┬──────┘
    │ state features (38, fixed-point)  │ address surfaces
    ▼                                   ▼
┌────────────────────────┐   ┌──────────────────────────────┐
│ V14 adaptive controller│   │ Semantic Address v2          │
│ 1,292 int8 params,     │   │ deterministic char-trigram   │
│ legal-mask structured  │   │ index over canonical packs   │
│ perceptron, 34 ops     │   │ (zero learned parameters)    │
└───┬────────────────────┘   └──────────────┬───────────────┘
    │ chosen typed operation                │ grounded candidates
    ▼                                       ▼
┌─────────────────────────────────────────────────────────┐
│ Exact typed micro-operations / specialists / tool plane │
│ (DISCOVER_DEPENDENTS, SATISFY_OBLIGATION, …)            │
└──────────────┬──────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────┐
│ Exact verifier  ── fail closed ──  5C root constraints  │
│ (9 immutable root classes; controller cannot rewrite    │
│  evidence, bypass verification, or self-integrate)      │
└──────────────┬──────────────────────────────────────────┘
               ▼
        grounded realization (evidence-copied answer)
```

## Model / controller structure

- One **1,292-byte int8 policy**: 34 exact operation classes × 38 generic
  features (obligation state, hypothesis match/conflict, claim contrast).
  Activations are fixed-point scaled by 256. Argument-aware: each action's
  argument candidate is scored with its own feature vector before a
  deterministic argmax `(score, −index, −op_id, args)`.
- The controller **contains no answer text and no learned world facts**; all
  factual content enters through the address substrate and the verifier.
- Selected over: a same-scale float structural variant (239/260) and an
  exact-certified DAgger variant (231/260). No capacity ladder above ~1K
  parameters was justified.

## Knowledge architecture

- Canonical packs: pinned public corpora (current: Simple English Wikipedia
  2026-07-01), content-hashed manifests, 397,196 documents / 275,989 canonical
  entities / 1,334,801 exact surfaces / 7.63 M hyperlink occurrences.
- Deployment representation (the only thing on the device card):
  `addressing-index.bin` (trigram postings + surface directory),
  `canonical-objects.bin`, `evidence.bin` (occurrence directory + blobs),
  `policy.json`, manifests — 1.15 GB total, sha256-verified on the device.
- Explicitly excluded from deployment: raw dumps, build databases, training
  artifacts, caches.

## COG, 5C, specialists, verifier

- **COG**: bounded explicit state (G8/O48/I16/H16/E64/U32/F32/S16); typed
  transitions only; `HALT_SUCCESS` fails closed while obligations are open.
- **5C**: nine immutable root constraint classes; root state cannot be
  rewritten by the learned path; activation of new components requires
  external authorization + signature + sandbox + tests + rollback.
- **Specialists**: COLD/WARM/HOT activation, deterministic or learned kinds
  with deterministic hard-limit clamps; shared parameter family with
  per-instance calibration.
- **Verifier**: exact acceptance gates realization; physical replay confirms
  zero verifier bypasses and zero unsupported answers.
- **Agent/tool plane**: bounded typed tool tasks (5/5 regressions), no
  unrestricted source synthesis, no automatic integration.

## Native runtime and hardware boundary

- `native/aethercore_runtime`: allocation-free C++17, stable C ABI
  (versioned structs, explicit sizes), no exceptions/RTTI.
  Wire snapshots: session 836 B; COG+5C+progress+specialists 180 B.
- Host↔device exactness is enforced by frozen ABI vectors (51/51 on hardware)
  and a witnessed 260-case trace replay (1,329/1,329 decisions, 107/107
  address queries, digests and entity sets exact).
- **Device A** (Waveshare touchscreen / Tactility appliance): UI/media only.
- **Device B** (AetherCore accessory): Waveshare ESP32-P4-WIFI6 SKU 32020,
  rev v1.3, 2×360 MHz RISC-V, 32 MiB PSRAM, 128 GB microSD on 4-bit SDMMC
  @ 20 MHz. Firmware: `firmware/p4_qualification`.

## Current limitations (measured, physical)

- Address query p50 **~1.09 s** on the qualification card (storage-bound;
  ~98% of wall time is page I/O; random 4 KiB reads ~24 IOPS / p50 36.6 ms).
- Evidence-directory layout amplifies I/O: flat sorted array + binary search
  ≈ 18 paged probes per entity lookup; 62% of misses at 1 MiB cache.
- 18/260 wrong-grounded selections remain (verifier still gates; they are
  wrong *grounded* answers, not unsupported answers).
- Only 360 MHz is a production CPU clock on rev v1.3; 200/300/400 MHz figures
  are documented interpolations.
- Power: unmeasured (no instrumentation).

## Current next steps

1. Evidence-directory layout: resident directory (4.4 MB, fits PSRAM) or
   2-level paged B-tree; expected to remove the majority of cache misses.
2. Faster storage qualification (eMMC-class) on the next board revision.
3. Keep the 128 GB microSD path as the qualification medium of record until
   then.
