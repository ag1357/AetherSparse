# Reproducing the Mission 6 entity lane

Use the authenticated Mission 5 replay bundle whose logical SHA-256 is
`099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`.
Extract it outside Git; the 137 MB replay is an input and must not be committed.

```bash
python scripts/droid/v11_entity_specialist.py freeze \
  --mission5-report reports/droid/v10/mission5-real-reachability.json.gz \
  --replay-bundle /path/to/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output-directory reports/droid/v11
```

The command selects only development/tuning `ENTITY_BINDING_WRONG` rows, checks
that all replay records are training-eligible, groups tier replicas under one
case, rejects cross-partition grouping, and emits deterministic gzip bytes.
Evaluation and final-held cases are neither copied nor scored.

The v0.5 source schema preserves occurrence-level hyperlinks, but the external
SQLite packs are not included in this checkpoint. Once an existing tier pack is
mounted, recover statistics only for residual mention surfaces:

```bash
python scripts/droid/v11_entity_specialist.py anchor-export \
  --pack /path/to/tier-pack.sqlite \
  --hard-negatives reports/droid/v11/ENTITY_HARD_NEGATIVES_V11.json.gz \
  --output /output/entity-anchor-statistics-TIER.json.gz \
  --alpha 1.0
```

Run the exporter once per tier. Keep the potentially large derived statistics
outside normal Git and integrate only their manifest/hash and measured baseline
summary. Do not replace missing occurrence data with distinct alias rows or
synthetic counts.
