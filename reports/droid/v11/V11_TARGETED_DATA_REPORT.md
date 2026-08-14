# Mission 6 targeted data handoff — completion report

Date: 2026-08-14 (UTC). Executor: Droid on the corpus host (Pi + s600).
Scope: `V11_TARGETED_DATA_HANDOFF.md` — two narrow training-side captures.
No broad battery, no retrieval rerun, no retraining, no pack/cache rebuilds.

## Integrity inputs (verified before use)

| input | expected SHA-256 | observed |
|---|---|---|
| replay bundle (logical) | `099cd28b…f0246` | match (Mission 5 export record) |
| Mission 5 report gz | `280b314b…27cd` | match (`reports/droid/v10/mission5-real-reachability.json.gz` @ `work/v11-integration` fd973da) |
| benchmark | `1e8b8942…d113` | match (all caches + freeze inputs) |

## Entity capture — PARTIAL (10k of 3 tiers)

- Freeze reproduced **byte-identically** on the corpus host:
  `ENTITY_HARD_NEGATIVES_V11.json.gz` gz sha256 `b544edbb…ec33ec` == the
  checked-in manifest. 346 replicas (153 development / 193 tuning), 175 unique
  cases; evaluation/final-held neither copied nor scored.
- `anchor-export` ran once against the **raw v0.5 10k corpus**
  (`simplewiki-v050-20260701-10k.sqlite`, user_version=500, anchors/redirects
  present): 126/152 residual mention surfaces covered, 345 statistics.
  Output: `entity-anchor-statistics-10k.json.gz` sha256 `51fc6382…47bb6b`
  (+ `.manifest.json`).
- **25k/397k not exported — source data does not exist.** The selector packs
  (`selector-*-p3.sqlite`, all tiers) carry only per-(source,target) `links`
  rows without anchor text or occurrence multiplicity; occurrence-level
  `anchors`/`redirects` exist solely in the raw v0.5 corpus build, and only the
  10k raw corpus survives on either machine. Producing 25k/397k anchor
  statistics requires rebuilding those raw corpora (forbidden here).
- Per handoff instruction, no alias rows or synthetic counts were substituted
  for the missing tiers.

## Value capture — COMPLETE (43 replicas, all three tiers)

- Scope: the 43 remaining `VALUE_NOT_ENUMERATED` development/tuning replicas
  from `reachability-rerun.json` (16 unique cases; 16×10k, 16×25k, 11×397k;
  16 development / 27 tuning). The diagnostic script filters on
  `failure_class`; the rerun file names it `old_failure_class`, so a lossless
  re-keyed projection was used as input (`value-remaining-43.json`, sha256
  `66203ba6…e72d9`, derivation recorded inside).
- Output: `value-enumeration-diagnostic-v11.json.gz` sha256
  `d73de7b3…0145c` (349,778 B gz / 2,519,253 B raw) + `.manifest.json`.
  `evaluation_and_final_held_used: false`.
- **Deviation — schema-translation sidecars.** The v11
  `SQLiteControllerProvider` requires the canonical v0.5 schema
  (anchors/redirects tables, `wiki_page_id`/`revision_id`/`raw_wikitext`
  columns, user_version=500); the selector packs use the legacy selector
  schema. The diagnostic performs only row-local lookups (8 selected chunks +
  gold source documents per replica) and pure-text region scanning, so
  per-tier sidecars containing exactly those rows were built:
  `sidecar-derivation-report.json` (sha256 `7465e767…21f1233e`).
  - Column mapping: `revision→revision_id`, `raw_text→raw_wikitext`,
    `content_hash→source_text_sha256` (verified `content_hash ==
    sha256(raw_text)`), `wiki_page_id` parsed from `mw:PAGE:REV:HASH` and
    cross-checked against the revision column.
  - Completeness: 123/123 (10k), 120/120 (25k), 86/86 (397k) chunks present;
    0 gold documents missing; 0 text-copy mismatches (byte-identical).
  - `anchors`/`redirects`/`aliases`/`chunks_fts` exist in the sidecars as
    EMPTY stub tables (provider presence check only; this code path never
    queries them). No corpus data was fabricated.
  - The diagnostic manifest's `pack_sha256_by_tier` records the SIDECAR
    hashes (`fb25b32c…` 10k, `61c75142…` 25k, `54e691a1…` 397k); the source
    selector pack hashes are in the derivation report (`4f232260…` 397k etc.).

## Files in this directory

| file | sha256 | notes |
|---|---|---|
| `ENTITY_HARD_NEGATIVES_V11.json.gz` | `b544edbb…ec33ec` | freeze output (input to anchor-export) |
| `ENTITY_HARD_NEGATIVES_V11.manifest.json` | `f8446446…52c7a6` | freeze manifest |
| `entity-anchor-statistics-10k.json.gz` | `51fc6382…47bb6b` | entity capture, 10k tier only |
| `entity-anchor-statistics-10k.json.gz.manifest.json` | `1ca7b855…404520` | anchor-export manifest |
| `value-enumeration-diagnostic-v11.json.gz` | `d73de7b3…0145c` | value capture, 43 replicas |
| `value-enumeration-diagnostic-v11.manifest.json` | `2cf5bbd5…966d23` | diagnostic manifest |
| `sidecar-derivation-report.json` | `7465e767…21f1233e` | sidecar provenance + verification |

## Not done (by constraint)

- 100k trace cache still absent (`/root/work/v08/trace-cache-100k.json`);
  not rebuilt.
- Entity anchor statistics for 25k/397k (raw corpora absent).
- No Wikipedia retrieval rerun, no product battery, no training.
