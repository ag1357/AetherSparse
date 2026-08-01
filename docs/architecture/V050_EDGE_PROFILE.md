# AetherSparse v0.5 flat edge workload profile

This profile measures the retained flat structured workload. It does not reuse
the rejected overlapping cognitive-cell pack, topology, routing-pool counts, or
hardware projections from v0.4.1.

## Evidence classes

The profiler keeps three kinds of evidence separate:

1. `host_measurement_not_edge_board_measurement` records host latency, process
   peak RSS, logical bounded reads, and Linux `/proc/self/io` physical-read
   deltas.
2. `analytical_projection_from_flat_workload_measurements` applies the measured
   p95 workload to the frozen P4 Pico, Core1106, RT700-class, and representative
   FPGA digital-twin assumptions in `aethersparse.v050.edge`.
3. A board measurement exists only when the independently frozen criteria set
   `p4_board_measured` to true. A projection is never labeled a board result.

For a cold-advised run the profiler calls `POSIX_FADV_DONTNEED` before opening
the pack. This is an advisory request. A successful call says that the kernel
accepted the request; it does not prove that every cached page was evicted. The
report records this limitation on every query. If the host lacks the advisory
or `/proc/self/io`, bounded cold reads are not considered measured for the
hardware purchase gate.

`peak_active_ram_bytes` is the process high-water mark from Linux `VmHWM`, with
`getrusage` as a fallback. It is deliberately labeled as a process peak rather
than a per-query allocation delta.

## Logical reads

The SQLite profile uses only `RealCorpusPack` bounded APIs. Result payloads are
rounded to SQLite pages and index probes are charged one SQLite page each. The
exact serialized result payload is also retained. This is a reproducible logical
page model, not a claim that SQLite exposed its internal cache-miss count.

The binary profile uses `FlatBinaryPackReader.query_sections`. Each selected
flat section is checksum-verified and counted as one logical block. Manifest,
index/core, and source/claim section bytes remain distinguishable. The declared
section limit fails before payload reads. Neither reader loads a corpus pack in
full.

Deterministic operation counts, model bytes, and neural MACs come from the
frozen query/runtime manifest. They are not inferred from advertised accelerator
TOPS. The v0.5 qualification query set does not instrument deterministic CPU
operations, so its report sets `operation_counter_instrumented` to `false` and
the purchase gate fails closed. A zero operation count must not be interpreted
as zero-cost CPU work.

## Query and criteria inputs

The query file is either an array or an object with a `queries` array. Every
query includes measured controller counters:

```json
{
  "queries": [
    {
      "query_id": "q:000001",
      "text": "When was Ada Lovelace born?",
      "title_queries": ["Ada Lovelace"],
      "alias_queries": ["Ada Lovelace"],
      "relation_families": ["birth date"],
      "entity_ids": ["entity:ada-lovelace"],
      "document_ids": ["simplewiki:123:456"],
      "claim_ids": ["claim:birth-date"],
      "source_binding_chunk_ids": ["chunk:birth-date"],
      "retrieval_limit": 8,
      "max_binary_sections": 32,
      "deterministic_ops": 18400,
      "neural_macs": 0,
      "model_bytes": 0
    }
  ]
}
```

Hardware decisions require a separately frozen criteria document. Omitting it
creates `UNQUALIFIED_DEFAULT_FAIL_CLOSED`, so the report cannot justify a
purchase.

```json
{
  "criteria_id": "V050_HARDWARE_PURCHASE_GATE_R1",
  "decision_profile_id": "simplewiki_v050_50k_binary",
  "architecture_qualified": false,
  "architecture_frozen": false,
  "neural_mapping_measured": false,
  "p4_board_measured": false,
  "latency_target_ms": 1000.0
}
```

## Progressive reproduction

Large packs remain outside Git. The command profiles both progressive scales and
selects exactly the profile named by the criteria for the single hardware
outcome:

```bash
PYTHONPATH=src python scripts/profile_v050_edge.py \
  --sqlite-pack simplewiki_v050_10k_sqlite=/artifacts/simplewiki-v050-10k.sqlite \
  --sqlite-pack simplewiki_v050_50k_sqlite=/artifacts/simplewiki-v050-50k.sqlite \
  --binary-pack simplewiki_v050_10k_binary=/artifacts/simplewiki-v050-10k.aeth \
  --binary-pack simplewiki_v050_50k_binary=/artifacts/simplewiki-v050-50k.aeth \
  --queries /artifacts/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.profile.json \
  --criteria /artifacts/v050-hardware-criteria.json \
  --repetitions 3 \
  --output /artifacts/v050-edge-profile.json
```

The JSON report contains each cold-advised and warm `QueryWorkload`, p50/p95
`FlatWorkloadProfile`, all four backend projections, the frozen criteria hash,
and exactly one `HardwareOutcome`.
