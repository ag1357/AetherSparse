# V12 exact claim address and P4 qualification

This lane requires the authenticated replay bundle with logical identity
`099cd28b5c2a090d1b08d1e07d39e59ae6583854971c539085741fbb369f0246`.
The script verifies the bundle and refuses any cohort row outside development or
tuning. The frozen benchmark is used only for post-selection recall scoring.

```bash
PYTHONPATH=src python scripts/droid/v12_claim_address_qualify.py \
  --bundle /path/to/controller-replay-3tier \
  --benchmark data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json \
  --cohort-report reports/droid/v11/upstream-reachability.json \
  --output reports/droid/v12/claim-address-p4-qualification.json \
  --page-bytes 4096
```

The output separates empirical Work-host timing, formula-derived byte/operation
proxies, and the unchanged v11 200/300/400 MHz analytical projections. The host
timings vary between runs and are not P4 evidence. The historic `flash_*` fields
in the digital twin mean parameterized external storage; they are not an eMMC
measurement. Re-run with a changed page size only as an explicit storage-layout
sensitivity, never as a claim about a device.

The v2 layout proxy places the query-local compact posting sidecar in PSRAM and
exact selected source regions on parameterized external storage. Each is charged
once. Every nonempty entity/relation/type posting region and every selected source
span deduplicated by `span_id` begins with one random page; only its continuation
pages are sequential. Transfers charge page-aligned physical bytes. Logical
payload bytes remain reported separately. The operation and SRAM formulas are
embedded in the JSON; they are analytical proxies, not runtime counters.

This replay does not contain full FTS postings, unselected chunk payloads, an
offline sparse-expansion index, or a whole-passage ANN index. It can qualify the
exact post-retrieval address selector but cannot certify full-pack claim retrieval.
Its query-local posting allocation excludes serialized directory bytes and cannot
be compared with the full-subsystem 8 MB PSRAM target.
