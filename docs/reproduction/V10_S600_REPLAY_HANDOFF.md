# AetherCore v10 S600 replay handoff

This is the only corpus-dependent preparation required before Work can run the
real Mission 5 reachability gate. It reuses the four existing v09 candidate
trace caches. Candidate retrieval is never invoked.

From a clean checkout of `work/aethercore-v10`, at the repository root:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v10-uv uv run python scripts/droid/v10_export_replay.py \
  --tier 10k=/root/work/artifacts/packs/selector-10k-p3.sqlite=/root/work/v08/trace-cache-10k.json \
  --tier 25k=/root/work/artifacts/packs/selector-25k-p3.sqlite=/root/work/v08/trace-cache-25k.json \
  --tier 100k=/root/work/artifacts/packs/selector-100k-p3.sqlite=/root/work/v08/trace-cache-100k.json \
  --tier 397k=/root/work/artifacts/packs/selector-full-p3.sqlite=/root/work/v08/ladder397/trace-cache-397k.json \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output /root/work/v10/controller-replay-four-tier
```

If an existing cache has a different location, change only the path after the
second `=` for that tier. Do not rebuild a missing cache during this handoff;
report the missing exact path instead. The script fails if any tier is omitted,
any replay case is incomplete, any input is missing, or bundle verification
fails.

Expected outputs:

- `/root/work/v10/controller-replay-four-tier/manifest.json`
- `/root/work/v10/controller-replay-four-tier/cases.jsonl.gz`
- `/root/work/v10/controller-replay-four-tier-staging/` with retained per-tier
  traces and verified tier bundles

Return the two final bundle files to Work. The manifest carries schema version,
input trace hashes, output hash, tier/partition counts, protected training
flags, and the complete-bundle hash.

The Work-side gate is then:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v10-uv uv run aethersparse controller qualify-reachability \
  --bundle /path/to/controller-replay-four-tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output reports/droid/v10/reachability-four-tier.json
```

Policy training, parameter sweeps, recurrence, cognitive-memory, sparse-head,
and quantization experiments remain prohibited until that report issues
`AETHERCORE_POLICY_FEASIBLE`.
