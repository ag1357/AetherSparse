# Reproduce the V11 targeted value qualification

This command consumes only the factory-supplied 43-row development/tuning
capture.  It does not retrieve Wikipedia, rebuild a pack, train a model, run a
product battery, or read evaluation/final-held labels.

```bash
UV_CACHE_DIR=/tmp/aethersparse-v11-value-upstream-uv uv run --extra dev python \
  scripts/droid/v11_value_upstream_qualify.py \
  --capture /path/to/value-enumeration-diagnostic-v11.json.gz \
  --manifest /path/to/value-enumeration-diagnostic-v11.manifest.json \
  --output reports/droid/v11/value-upstream-qualification.json
```

The command fails closed when:

- compressed or uncompressed capture identity differs from the manifest;
- any partition is not development or tuning;
- the capture is not exactly 43 replicas grouped into 16 cases;
- a selected chunk or compiler source document is absent;
- selected text does not match its absolute character offsets;
- pre/post region, deduplication, or cap fields are missing;
- any emitted runtime match fails exact-surface or document rebinding.

The first-loss classifier uses accepted development/tuning values only as an
offline measurement probe.  It is not imported by the production controller,
does not choose a feature, and emits only aggregate counts.  A target-present
extraction residual may justify a deterministic correction only when the
development partition supplies the same failure family.  This capture has no
such development residual, so no quotation change and no neural span model are
qualified.

## Rerun the exact targeted residual

After applying the generic typed-value and canonicalization corrections, rerun
only the same 43 replay rows:

```bash
UV_CACHE_DIR=/tmp/aethersparse-v11-value-upstream-uv uv run --extra dev python \
  scripts/droid/v11_value_residual_rerun.py \
  --replay-archive /path/to/controller-replay-3tier-export.tar.gz \
  --capture /path/to/value-enumeration-diagnostic-v11.json.gz \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --output reports/droid/v11/value-targeted-residual-rerun.json
```

The rerun verifies the archive member set, logical replay identity, benchmark
identity, and development/tuning-only partition boundary before selecting the
43 keys.  It uses the existing certified search bounds: maximum depth 14,
4,096 expansions, beam width 32, and argument cap 64.  It emits aggregate
counts only and does not train a model or run a full-corpus battery.

The expected result is 34 exact goals possible and 32 certified reachable:
14/16 development and 18/27 tuning.  The 11-row residual is three tuning-only
dual extraction misses, six tuning rows with missing target source chunks, and
two development rows with goal-present bounded-search failures.  In the JSON,
the two bounded-search rows refine the `SOURCE_CHUNK_ABSENT=8` group and are not
additional rows.
