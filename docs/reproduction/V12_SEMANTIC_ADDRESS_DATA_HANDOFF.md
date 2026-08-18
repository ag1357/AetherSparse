# Mission 7 minimal Factory/S600 Semantic Address v2 handoff

This is the **one** external-data handoff required by Mission 7.  It is not a
new benchmark battery or retrieval run.  It reads the existing canonical v0.5
SQLite packs immutably and compiles the address/training substrate that is
absent in Work.

## Required inputs

- `ENTITY_HARD_NEGATIVES_V11.json.gz`, SHA-256
  `b544edbb46570d09c6efc415bd77806f24331efa655f93682ebab28c40ec33ec`;
- canonical 10k v0.5 SQLite pack, SHA-256
  `cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5`;
- the existing 25k and 397k canonical v0.5 SQLite packs used by the v09/v10
  tier runs. Their identities are recorded by the newly generated manifests;
- this repository at or after the Mission 7 Lane A checkpoint.

Do not supply evaluation/final-held rows or labels. Do not copy any raw SQLite
pack, dump, trace cache, or benchmark file into the returned directory.

## Exact commands

Set these four paths to the existing Factory artifacts:

```bash
export PACK_10K=/root/work/artifacts/packs/selector-10k-p3.sqlite
export PACK_25K=/root/work/artifacts/packs/selector-25k-p3.sqlite
export PACK_397K=/root/work/artifacts/packs/selector-full-p3.sqlite
export HARD_NEGATIVES=/root/work/v11/ENTITY_HARD_NEGATIVES_V11.json.gz
export V12_OUT=/root/work/v12/semantic-address-v2-targeted

test "$(sha256sum "$HARD_NEGATIVES" | cut -d' ' -f1)" = \
  b544edbb46570d09c6efc415bd77806f24331efa655f93682ebab28c40ec33ec
test "$(sha256sum "$PACK_10K" | cut -d' ' -f1)" = \
  cb93db732eaf314806700b38ef7ec9d5cf85dea69f32deb51f97a3aa890023e5

PYTHONPATH=src python scripts/droid/v12_address_data.py compile-pack \
  --pack "$PACK_10K" --tier 10k --output "$V12_OUT/address/10k"
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-pack \
  --pack "$PACK_25K" --tier 25k --output "$V12_OUT/address/25k"
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-pack \
  --pack "$PACK_397K" --tier 397k --output "$V12_OUT/address/397k"

# Verified exact-FST adapters consume only fit occurrence priors by default.
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-exact \
  --address-export "$V12_OUT/address/10k" \
  --source-splits fit --consumer-phase fit \
  --output "$V12_OUT/exact/10k.fst"
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-exact \
  --address-export "$V12_OUT/address/25k" \
  --source-splits fit --consumer-phase fit \
  --output "$V12_OUT/exact/25k.fst"
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-exact \
  --address-export "$V12_OUT/address/397k" \
  --source-splits fit --consumer-phase fit \
  --output "$V12_OUT/exact/397k.fst"

PYTHONPATH=src python scripts/droid/v12_address_data.py export-v11-benchmark \
  --pack "$PACK_10K" --hard-negatives "$HARD_NEGATIVES" --tier 10k \
  --output "$V12_OUT/capture/10k.jsonl.gz"
PYTHONPATH=src python scripts/droid/v12_address_data.py export-v11-benchmark \
  --pack "$PACK_25K" --hard-negatives "$HARD_NEGATIVES" --tier 25k \
  --output "$V12_OUT/capture/25k.jsonl.gz"
PYTHONPATH=src python scripts/droid/v12_address_data.py export-v11-benchmark \
  --pack "$PACK_397K" --hard-negatives "$HARD_NEGATIVES" --tier 397k \
  --output "$V12_OUT/capture/397k.jsonl.gz"

PYTHONPATH=src python scripts/droid/v12_address_data.py compile-benchmark \
  --capture "$V12_OUT/capture/10k.jsonl.gz" \
  --output "$V12_OUT/benchmark/10k"
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-benchmark \
  --capture "$V12_OUT/capture/25k.jsonl.gz" \
  --output "$V12_OUT/benchmark/25k"
PYTHONPATH=src python scripts/droid/v12_address_data.py compile-benchmark \
  --capture "$V12_OUT/capture/397k.jsonl.gz" \
  --output "$V12_OUT/benchmark/397k"

PYTHONPATH=src python scripts/droid/v12_address_data.py finalize-handoff \
  --root "$V12_OUT" \
  --output /root/work/v12/semantic-address-v2-targeted.manifest.json
```

The `export-v11-benchmark` command unions title, redirect, alias, and anchor
channels before retention and records a rank for every channel contribution.
Each contribution carries a raw, channel-specific score separately from a
bounded `[0,1]` channel score, plus immutable provenance IDs; consumers must
not silently reinterpret raw support as a probability. Export channel names
map explicitly to fusion names (`title -> exact_title`,
`anchor -> anchor_prior`).
It does not run fuzzy or semantic ANN generation; those are separate Mission 7
channels. A case-level label becomes a per-mention alignment only when exactly
one mention and one required canonical entity exist. All other associations
are quarantined instead of guessed.

Every v2 stream row has a deterministic content-addressed `record_id`. The
entity stream is the sole ID/title authority: loaders reject noncanonical IDs,
titles that do not hash to their claimed IDs, and any alias, redirect, or
occurrence whose canonical pair disagrees with that registry. The verified
bundle identity is the SHA-256 of `manifest.json` plus its source-pack and
per-stream identities. Exact FST manifests bind to that manifest SHA rather
than to an untyped input path.

`surface_statistics.jsonl.gz` contains three independently recomputed views:

| view | included source splits | lawful use |
|---|---|---|
| `fit` | `fit` | fitting and selection-time priors |
| `fit+calibration` | `fit`, `calibration` | frozen-system holdout qualification only |
| `all` | `fit`, `calibration`, `holdout` | descriptive audit only |

Support, unique source-document counts, diversity, entropy, unresolved mass,
and `P(entity|mention)` are recomputed inside each view. The fit/selection
loaders reject either wider view, and holdout qualification rejects `all`.
Holdout-only surfaces appear only in `all`, where they can be counted without
exposing their identities to fitting.

## Return exactly

Return the `semantic-address-v2-targeted/` directory and its sibling
`semantic-address-v2-targeted.manifest.json`. Every compiled stream is gzip
compressed, deterministic, and individually hashed by its local manifest.
The closed record contract is `schemas/semantic-address-v2.schema.json`; the
closed manifest contract is
`schemas/semantic-address-v2-manifest.schema.json`.
The outer manifest hashes the entire handoff and rejects raw pack extensions or
symlinks.

The consumer must verify all manifests before reading row content. Development
labels may fit. Tuning labels are physically separated and may only calibrate,
select, and score a frozen candidate system. Evaluation/final-held labels must
remain absent.
