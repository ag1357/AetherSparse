# Reproduce the Mission 7 exact FST/prior qualification

The qualification uses only development rows to construct the measured targeted
index. Tuning is read after construction for label-free title-transfer
measurement. Evaluation and final-held rows are authenticated against the replay
partition map and excluded before address evidence is created.

```bash
export PYTHONPATH=src
python scripts/droid/v12_fst_prior_qualify.py \
  --candidate-diagnostic /path/to/candidate-diagnostic-397k.jsonl.gz \
  --candidate-manifest /path/to/candidate-diagnostic-397k.manifest.json \
  --replay-bundle /path/to/authenticated/controller-replay-3tier \
  --entity-catalog data/normalized/entity_catalog.json \
  --artifact-dir /external/path/v12-fst-prior \
  --output reports/droid/v12/fst-prior-qualification.json \
  --markdown reports/droid/v12/EXACT_FST_PRIOR_QUALIFICATION.md
```

Required authenticated inputs:

- replay bundle: `099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`;
- candidate diagnostic gzip: `8dfb6c9a723a66d9dfd7d24a102a719a87b590a457fc1bab505cced771d57158`;
- committed non-benchmark entity catalog: `c1e04e33ab39b4d177ff1c535527e9c2f02307c4160faefd8867b8fe5126c106`.

The generated `.fst` and manifest stay outside normal Git. Their exact hashes
and measured byte footprints are recorded in
`reports/droid/v12/fst-prior-qualification.json`. The same compiler accepts
full-corpus title, alias, redirect, and anchor `AddressEvidence` rows when the
Factory export becomes available; the targeted diagnostic does not contain
those missing channels.
