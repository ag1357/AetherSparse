#!/usr/bin/env python3
"""Qualify exact claim addressing and its analytical ESP32-P4 cost.

The replay experiment is a post-retrieval selection ablation: retained replay
claims include evidence-oracle state, so it may compare direct addressing with
the repaired v11 claim pool over retained weighted-FTS/BM25-selected evidence
but may not certify pack retrieval.
Only the Mission 5 development/tuning failure cohort is read.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from aethersparse.controller.answering import make_answer_plan, realize_plan, select_answer
from aethersparse.controller.claim_address import (
    ClaimAddressIndex,
    ClaimAddressLookup,
    evidence_records_from_replay,
)
from aethersparse.controller.micro_ops import state_from_replay
from aethersparse.controller.models import EvidenceRecord, QueryFrame
from aethersparse.controller.replay import ReplayCase, verify_replay_bundle
from aethersparse.controller.search import canonical_answer_match, canonicalize
from aethersparse.controller.value_repair import repair_state_with_typed_values
from aethersparse.controller.verification import verify_realization
from aethersparse.specialists.address_p4 import AddressQueryCost, project_address_cost
from aethersparse.specialists.p4_cost import (
    V11_P4_CALIBRATION_ID,
    v11_reference_assumptions,
)

TRAINING_PARTITIONS = frozenset({"development", "tuning"})
KS = (1, 4, 8, 16, 32)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _cohort(path: Path) -> dict[tuple[str, str], str]:
    report = _read_object(path)
    rows = report.get("per_case")
    if not isinstance(rows, list):
        raise ValueError("cohort report lacks per_case rows")
    cohort: dict[tuple[str, str], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        partition = str(row.get("partition", ""))
        if partition not in TRAINING_PARTITIONS:
            raise ValueError("protected partition entered claim-address cohort")
        cohort[(str(row["case_id"]), str(row["corpus_tier"]))] = partition
    if len(cohort) != 695:
        raise ValueError(f"expected unchanged 695-state cohort, received {len(cohort)}")
    return cohort


def _benchmark(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_object(path)
    rows = payload.get("cases")
    if not isinstance(rows, list):
        raise ValueError("benchmark lacks cases")
    return {
        str(row["case_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("partition") in TRAINING_PARTITIONS
    }


def _record_surface(record: EvidenceRecord, shape: str) -> str:
    claim = record.claim
    if shape == "quotation" and claim.quotation:
        return claim.quotation
    return claim.object_value or claim.quantity_value or claim.quotation or ""


def _goal_present(
    records: tuple[EvidenceRecord, ...],
    accepted: tuple[str, ...],
    shape: str,
) -> bool:
    values = tuple(_record_surface(record, shape) for record in records)
    if shape == "list":
        parts = [part.strip() for answer in accepted for part in answer.split(";") if part.strip()]
        return bool(parts) and all(
            any(canonical_answer_match((value,), (part,)) for value in values) for part in parts
        )
    if shape == "comparison":
        accepted_canonical = tuple(canonicalize(answer) for answer in accepted)
        matching = {
            canonicalize(value)
            for value in values
            if any(canonicalize(value) in answer for answer in accepted_canonical)
        }
        return len(matching) >= 2
    return any(canonical_answer_match((value,), accepted) for value in values)


def _blind_verification(
    query_id: str,
    frame: QueryFrame,
    lookup: ClaimAddressLookup,
) -> tuple[bool, str | None]:
    graph = lookup.evidence_graph(query_id, frame)
    selection = select_answer(frame, graph)
    if selection is None:
        return False, None
    plan = make_answer_plan(selection, graph)
    answer = realize_plan(plan)
    verification = verify_realization(frame, graph, plan, answer)
    return verification.passed, answer.text if verification.passed else None


def _address_cost(
    index: ClaimAddressIndex,
    frame: QueryFrame,
    lookup: ClaimAddressLookup,
    *,
    candidate_pool_size: int,
    page_bytes: int,
) -> AddressQueryCost:
    key_bytes = sum(len(item.encode("utf-8")) for item in frame.candidate_entity_ids)
    key_bytes += sum(len(item.encode("utf-8")) for item in frame.requested_relation_families)
    selected = lookup.candidate_count_after_cap

    def page_accesses(regions: tuple[int, ...]) -> tuple[int, int, int]:
        pages = tuple(max(1, math.ceil(size / page_bytes)) for size in regions)
        random_pages = len(pages)
        sequential_pages = sum(max(0, count - 1) for count in pages)
        return random_pages, sequential_pages, sum(pages) * page_bytes

    psram_random, psram_sequential, psram_transfer = page_accesses(
        lookup.posting_region_payload_bytes
    )
    external_random, external_sequential, external_transfer = page_accesses(
        lookup.source_region_payload_bytes
    )
    # Formula-derived scalar proxy, not an instruction counter: four operations
    # per query-key byte, eight per eligible posting record, twelve per bounded
    # comparison in an n*ceil(log2(n)) sort proxy, and one per copied source byte.
    eligible = lookup.candidate_count_before_cap
    comparison_proxy = eligible * math.ceil(math.log2(eligible)) if eligible > 1 else 0
    integer_operations = (
        4 * key_bytes + 8 * eligible + 12 * comparison_proxy + lookup.source_region_bytes_read
    )
    has_page_transfer = bool(psram_transfer or external_transfer)
    return AddressQueryCost(
        operation_id="claim-address.direct-v1",
        page_bytes=page_bytes,
        internal_sram_dma_peak_bytes=(page_bytes if has_page_transfer else 256) + 64 * selected,
        psram_resident_posting_bytes=index.manifest.posting_serialized_bytes,
        # The query-local posting sidecar is the entire PSRAM allocation in this
        # proxy; the result vector and one reusable page buffer remain in SRAM.
        psram_peak_known_allocation_bytes=index.manifest.posting_serialized_bytes,
        fst_payload_bytes_read=0,
        posting_payload_bytes_read=lookup.posting_bytes_read,
        query_key_bytes_processed=key_bytes,
        bq_payload_bytes_read=0,
        pq_payload_bytes_read=0,
        int8_payload_bytes_read=0,
        source_region_payload_bytes_read=lookup.source_region_bytes_read,
        psram_page_aligned_transfer_bytes=psram_transfer,
        external_page_aligned_transfer_bytes=external_transfer,
        psram_random_page_reads=psram_random,
        psram_sequential_page_reads=psram_sequential,
        external_random_page_reads=external_random,
        external_sequential_page_reads=external_sequential,
        formula_derived_integer_operations=integer_operations,
        xor_popcount_operations=0,
        simd_operations=0,
        neural_macs=0,
        candidates_before_address=candidate_pool_size,
        candidates_after_address=lookup.candidate_count_before_cap,
        candidates_after_cap=selected,
        active_parameters=0,
        model_bytes=0,
    )


def _summarize(values: list[float]) -> dict[str, float]:
    return {
        "mean": sum(values) / len(values) if values else 0.0,
        "p50": median(values) if values else 0.0,
        "p95": _percentile(values, 0.95),
        "max": max(values, default=0.0),
    }


def qualify(
    bundle: Path,
    benchmark_path: Path,
    cohort_report: Path,
    *,
    page_bytes: int = 4096,
) -> dict[str, Any]:
    if page_bytes < 512:
        raise ValueError("page bytes must be at least 512")
    manifest = verify_replay_bundle(bundle)
    cohort = _cohort(cohort_report)
    benchmark = _benchmark(benchmark_path)
    cases: dict[tuple[str, str], ReplayCase] = {}
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as stream:
        for line in stream:
            case = ReplayCase.model_validate_json(line)
            key = (case.case_id, case.corpus_tier)
            if key not in cohort:
                continue
            if case.partition not in TRAINING_PARTITIONS or not case.training_eligible:
                raise ValueError(f"protected or ineligible replay row entered: {key}")
            if cohort[key] != case.partition:
                raise ValueError(f"partition drift: {key}")
            cases[key] = case
    if set(cases) != set(cohort):
        raise ValueError("authenticated replay does not contain the full cohort")

    counts: Counter[str] = Counter()
    recall: dict[str, Counter[int]] = {
        "repaired_v11_claim_pool_over_retained_fts_bm25_selected_evidence": Counter(),
        "direct_entity_relation_type_address": Counter(),
        "direct_then_unresolved_fts_bm25_fallback": Counter(),
    }
    costs: list[AddressQueryCost] = []
    lookup_ms: list[float] = []
    build_ms: list[float] = []
    blind_answer_correct = 0
    sidecar_bytes: list[float] = []
    lattice_candidates: list[float] = []
    baseline_candidates: list[float] = []
    direct_candidates: list[float] = []
    fallback_activations = 0
    partition_counts: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()

    for key in sorted(cases):
        case = cases[key]
        gold = benchmark.get(case.case_id)
        if gold is None or gold.get("partition") != case.partition:
            raise ValueError(f"missing or mismatched training benchmark row: {key}")
        if str(gold.get("accepted_disposition", "")) != "ANSWER":
            raise ValueError(f"Mission 5 failure cohort includes non-answer row: {key}")
        accepted = tuple(str(item) for item in gold.get("accepted_answers", ()))
        shape = str(gold.get("required_answer_shape", "unknown"))
        state = repair_state_with_typed_values(state_from_replay(case)).state
        frame = QueryFrame.model_validate(state.frame)
        records = evidence_records_from_replay(state.claims, state.source_spans)

        build_started = time.perf_counter_ns()
        index = ClaimAddressIndex(records)
        build_ms.append((time.perf_counter_ns() - build_started) / 1_000_000.0)
        lookup_started = time.perf_counter_ns()
        direct = index.lookup(frame, limit=32)
        lookup_ms.append((time.perf_counter_ns() - lookup_started) / 1_000_000.0)

        unresolved = bool(
            direct.unresolved_entity_ids or direct.unresolved_relation_ids or not direct.records
        )
        fallback_records = records[:32] if unresolved else direct.records
        fallback_activations += int(unresolved)
        variants = {
            "repaired_v11_claim_pool_over_retained_fts_bm25_selected_evidence": records,
            "direct_entity_relation_type_address": direct.records,
            "direct_then_unresolved_fts_bm25_fallback": fallback_records,
        }
        for name, selected in variants.items():
            for candidate_k in KS:
                recall[name][candidate_k] += int(
                    _goal_present(selected[:candidate_k], accepted, shape)
                )
        verified, answer = _blind_verification(case.case_id, frame, direct)
        counts["blind_verifier_pass"] += int(verified)
        blind_answer_correct += int(
            bool(answer) and canonical_answer_match((str(answer),), accepted)
        )
        counts["direct_nonempty"] += int(bool(direct.records))
        counts["direct_unresolved_entity"] += int(bool(direct.unresolved_entity_ids))
        counts["direct_unresolved_relation"] += int(bool(direct.unresolved_relation_ids))
        counts["cases"] += 1
        partition_counts[case.partition] += 1
        tier_counts[case.corpus_tier] += 1
        sidecar_bytes.append(float(index.manifest.posting_serialized_bytes))
        baseline_candidates.append(float(len(records)))
        direct_candidates.append(float(len(direct.records)))
        lattice_candidates.append(float(len(direct.value_lattice().candidates)))
        costs.append(
            _address_cost(
                index,
                frame,
                direct,
                candidate_pool_size=len(records),
                page_bytes=page_bytes,
            )
        )

    case_count = counts["cases"]
    scenarios: dict[str, dict[str, Any]] = {}
    for name, assumptions in v11_reference_assumptions().items():
        projections = [project_address_cost(cost, assumptions) for cost in costs]
        scenarios[name] = {
            "clock_mhz": assumptions.clock_mhz,
            "calibration_id": V11_P4_CALIBRATION_ID,
            "evidence_class": "analytical_projection_not_hardware_measurement",
            "external_storage_scope": ("v11_parameterized_reference; not an eMMC specification"),
            "external_storage_bandwidth_mb_s": assumptions.flash_bandwidth_mb_s,
            "external_storage_random_access_us": assumptions.flash_random_access_us,
            "projected_latency_ms": _summarize([item.virtual_latency_ms for item in projections]),
            "compute_ms": _summarize([item.compute_ms for item in projections]),
            "psram_transfer_ms": _summarize([item.psram_transfer_ms for item in projections]),
            "external_storage_transfer_ms": _summarize(
                [item.external_storage_transfer_ms for item in projections]
            ),
            "random_access_ms": _summarize([item.random_access_ms for item in projections]),
        }

    def counted(field: str) -> dict[str, float]:
        return _summarize([float(getattr(cost, field)) for cost in costs])

    recall_payload = {
        name: {f"recall_at_{candidate_k}": values[candidate_k] / case_count for candidate_k in KS}
        for name, values in recall.items()
    }
    return {
        "schema_version": "aethersparse.v12-claim-address-p4-qualification.v2",
        "status": "COMPLETE_POST_RETRIEVAL_SELECTION_ABLATION",
        "decision": "DIRECT_CLAIM_ADDRESS_INFRASTRUCTURE_QUALIFIED_RETRIEVAL_NOT_QUALIFIED",
        "base_commit": "a7dcb187a985164648549eb18f67a7a6a4a964c6",
        "scope": {
            "cohort": "unchanged Mission 5 695 development/tuning failures",
            "partitions": dict(sorted(partition_counts.items())),
            "tiers": dict(sorted(tier_counts.items())),
            "evaluation_final_held_consumed": False,
            "constructor_gold_consumed": False,
            "accepted_answers_used_posthoc_metrics_only": True,
            "replay_evidence_is_oracle_contaminated": True,
            "claim": ("selection over retained source-bound replay evidence, not pack retrieval"),
        },
        "source_identity": {
            "replay_bundle_sha256": manifest.bundle_sha256,
            "benchmark_sha256": _sha256(benchmark_path),
            "cohort_report_sha256": _sha256(cohort_report),
        },
        "reproducibility": {
            "deterministic_fields": "all fields except host_measurement",
            "nondeterministic_fields": [
                "host_measurement.index_build_ms",
                "host_measurement.lookup_ms",
            ],
            "reason": "empirical Work-host timing varies between runs",
        },
        "case_count": case_count,
        "recall": recall_payload,
        "blind_verifier": {
            "passes": counts["blind_verifier_pass"],
            "posthoc_canonical_correct": blind_answer_correct,
        },
        "address_resolution": {
            "nonempty": counts["direct_nonempty"],
            "unresolved_entity": counts["direct_unresolved_entity"],
            "unresolved_relation": counts["direct_unresolved_relation"],
            "fallback_activations": fallback_activations,
        },
        "candidate_counts": {
            "baseline_records": _summarize(baseline_candidates),
            "direct_records": _summarize(direct_candidates),
            "typed_lattice_records": _summarize(lattice_candidates),
        },
        "host_measurement": {
            "evidence_class": "measured_work_host_not_p4",
            "index_build_ms": _summarize(build_ms),
            "lookup_ms": _summarize(lookup_ms),
        },
        "formula_derived_analytical_cost": {
            "evidence_class": "formula_derived_analytical_proxy_not_runtime_counters",
            "physical_layout": "psram_postings_external_source_regions_v1",
            "transfer_accounting": "page_aligned_physical_bytes_not_logical_payload_bytes",
            "cache_model": (
                "deduplicate selected exact spans by span_id within each query; "
                "no cross-query source cache credit"
            ),
            "directory_accounting": (
                "query-key bytes processed are reported; serialized directory bytes are "
                "not represented by this replay proxy"
            ),
            "operation_formula": (
                "4*query_key_bytes + 8*eligible_pre_cap_records + "
                "12*(n*ceil(log2(n))) + deduplicated_source_payload_bytes"
            ),
            "sram_formula": (
                "one reusable page buffer when any page is read, else 256 bytes, "
                "+ 64 bytes per selected record"
            ),
            "page_formula": (
                "each nonempty posting or selected deduplicated source region begins with "
                "one random page; only pages after its first are sequential"
            ),
            "page_bytes": page_bytes,
            "internal_sram_dma_peak_bytes": counted("internal_sram_dma_peak_bytes"),
            "psram_resident_query_local_posting_bytes": _summarize(sidecar_bytes),
            "psram_peak_known_allocation_bytes": counted("psram_peak_known_allocation_bytes"),
            "fst_payload_bytes_read": counted("fst_payload_bytes_read"),
            "posting_payload_bytes_read": counted("posting_payload_bytes_read"),
            "centroid_payload_bytes_read": _summarize([0.0] * case_count),
            "serialized_directory_bytes_read": None,
            "query_key_bytes_processed": counted("query_key_bytes_processed"),
            "bq_payload_bytes_read": counted("bq_payload_bytes_read"),
            "pq_payload_bytes_read": counted("pq_payload_bytes_read"),
            "int8_payload_bytes_read": counted("int8_payload_bytes_read"),
            "deduplicated_source_region_payload_bytes_read": counted(
                "source_region_payload_bytes_read"
            ),
            "psram_page_aligned_transfer_bytes": counted("psram_page_aligned_transfer_bytes"),
            "external_page_aligned_transfer_bytes": counted("external_page_aligned_transfer_bytes"),
            "total_page_aligned_transfer_bytes": counted("page_aligned_transfer_bytes"),
            "psram_random_4kb_page_reads": counted("psram_random_page_reads"),
            "psram_sequential_4kb_page_reads": counted("psram_sequential_page_reads"),
            "external_random_4kb_page_reads": counted("external_random_page_reads"),
            "external_sequential_4kb_page_reads": counted("external_sequential_page_reads"),
            "random_4kb_page_reads": counted("random_page_reads"),
            "sequential_4kb_page_reads": counted("sequential_page_reads"),
            "formula_derived_integer_operations": counted("formula_derived_integer_operations"),
            "xor_popcount_operations": counted("xor_popcount_operations"),
            "simd_operations": counted("simd_operations"),
            "neural_macs": counted("neural_macs"),
            "candidates_before_address": counted("candidates_before_address"),
            "candidates_after_address_pre_cap": counted("candidates_after_address"),
            "candidates_after_cap": counted("candidates_after_cap"),
            "active_parameters": counted("active_parameters"),
            "model_bytes": counted("model_bytes"),
        },
        "p4_projection": {
            "calibration_id": V11_P4_CALIBRATION_ID,
            "scenarios": scenarios,
            "actual_p4_hardware_measurement": None,
        },
        "pareto": [
            {
                "system": "direct_entity_relation_type_address",
                "recall_at_16": recall_payload["direct_entity_relation_type_address"][
                    "recall_at_16"
                ],
                "nominal_p95_ms": scenarios["nominal_300mhz"]["projected_latency_ms"]["p95"],
                "cost_status": "formula_derived_and_analytically_projected",
            },
            {
                "system": "repaired_v11_claim_pool_over_retained_fts_bm25_selected_evidence",
                "recall_at_16": recall_payload[
                    "repaired_v11_claim_pool_over_retained_fts_bm25_selected_evidence"
                ]["recall_at_16"],
                "nominal_p95_ms": None,
                "cost_status": (
                    "not identifiable: replay omits FTS postings and unselected chunk bytes"
                ),
            },
            {
                "system": "direct_then_unresolved_fts_bm25_fallback",
                "recall_at_16": recall_payload["direct_then_unresolved_fts_bm25_fallback"][
                    "recall_at_16"
                ],
                "nominal_p95_ms": None,
                "cost_status": "fallback retrieval cost not identifiable from replay",
            },
        ],
        "ablations_not_run": {
            "offline_sparse_expansion": "no lawful full pack or expansion index in Work",
            "whole_passage_ann_fallback": "no lawful full passage ANN index in Work",
        },
        "limitations": [
            "Retained replay claims include evidence-oracle state.",
            (
                "The comparator is the repaired v11 claim pool over retained FTS/BM25-selected "
                "evidence, not raw FTS/BM25 or a fresh pack rerun."
            ),
            (
                "Query-local posting bytes exclude the serialized directory and are not a "
                "full-corpus claim-index or full address-subsystem footprint."
            ),
            "External-storage values reuse the v11 reference model and are not eMMC figures.",
            "No S600/full-corpus battery, evaluation, final-held, training, or ANN run occurred.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--cohort-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-bytes", type=int, default=4096)
    args = parser.parse_args()
    result = qualify(
        args.bundle,
        args.benchmark,
        args.cohort_report,
        page_bytes=args.page_bytes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
