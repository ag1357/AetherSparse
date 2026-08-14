#!/usr/bin/env python3
"""Measure host value-repair work and project it analytically to ESP32-P4."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from pathlib import Path
from statistics import median
from typing import Any

from aethersparse.controller.micro_ops import MicroState, state_from_replay
from aethersparse.controller.replay import ReplayCase, verify_replay_bundle
from aethersparse.controller.value_repair import ValueRepairResult, repair_state_with_typed_values
from aethersparse.specialists.p4_cost import P4Assumptions, P4OperationCost, project_p4


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _source_bytes(state: MicroState, repair: ValueRepairResult) -> int:
    return (
        sum(len(str(span.get("text", "")).encode()) for span in state.source_spans)
        if repair.scanned_source_spans
        else 0
    )


def _operation_cost(state: MicroState, repair: ValueRepairResult) -> P4OperationCost:
    source_bytes = _source_bytes(state, repair)
    largest_span = max(
        (len(str(span.get("text", "")).encode()) for span in state.source_spans),
        default=0,
    )
    return P4OperationCost(
        operation_id="value.typed-exact-scan.v11",
        integer_operations=source_bytes + 16 * repair.proposed_hypotheses,
        macs=0,
        memory_bytes=source_bytes,
        psram_bytes=source_bytes,
        flash_bytes=0,
        psram_accesses=repair.scanned_source_spans,
        flash_accesses=0,
        random_psram_reads=0,
        random_flash_reads=0,
        sequential_reads=repair.scanned_source_spans,
        scratch_ram_bytes=largest_span + min(repair.proposed_hypotheses, 64) * 256,
        model_bytes=0,
    )


def _assumptions() -> dict[str, P4Assumptions]:
    return {
        "conservative_200mhz": P4Assumptions(
            clock_mhz=200,
            integer_ops_per_cycle=1.0,
            macs_per_cycle=1.0,
            psram_bandwidth_mb_s=20.0,
            flash_bandwidth_mb_s=5.0,
            psram_random_access_us=2.0,
            flash_random_access_us=100.0,
        ),
        "nominal_300mhz": P4Assumptions(
            clock_mhz=300,
            integer_ops_per_cycle=1.0,
            macs_per_cycle=1.0,
            psram_bandwidth_mb_s=40.0,
            flash_bandwidth_mb_s=10.0,
            psram_random_access_us=1.0,
            flash_random_access_us=60.0,
        ),
        "optimistic_plausible_400mhz": P4Assumptions(
            clock_mhz=400,
            integer_ops_per_cycle=1.0,
            macs_per_cycle=1.0,
            psram_bandwidth_mb_s=80.0,
            flash_bandwidth_mb_s=20.0,
            psram_random_access_us=0.5,
            flash_random_access_us=30.0,
        ),
    }


def qualify(
    bundle: Path,
    reachability_report: Path,
    *,
    warmups: int,
    trials: int,
) -> dict[str, Any]:
    if warmups < 0 or trials < 1:
        raise ValueError("warmups must be non-negative and trials positive")
    manifest = verify_replay_bundle(bundle)
    reachability = json.loads(reachability_report.read_text(encoding="utf-8"))
    keys = {
        (str(row["case_id"]), str(row["corpus_tier"]))
        for row in reachability["per_case"]
    }
    states: list[MicroState] = []
    with gzip.open(bundle / manifest.cases_file, "rt", encoding="utf-8") as handle:
        for line in handle:
            case = ReplayCase.model_validate_json(line)
            if (case.case_id, case.corpus_tier) in keys:
                if case.partition not in {"development", "tuning"} or not case.training_eligible:
                    raise ValueError("protected state entered P4 qualification")
                states.append(state_from_replay(case))
    states.sort(key=lambda item: item.case_id)
    if len(states) != len(keys):
        raise ValueError("P4 qualification state count mismatch")
    for _ in range(warmups):
        for state in states:
            repair_state_with_typed_values(state)
    host_ms: list[float] = []
    for _ in range(trials):
        start = time.perf_counter_ns()
        for state in states:
            repair_state_with_typed_values(state)
        host_ms.append((time.perf_counter_ns() - start) / 1_000_000.0)
    repairs = [repair_state_with_typed_values(state) for state in states]
    costs = [_operation_cost(state, repair) for state, repair in zip(states, repairs, strict=True)]
    active_indices = [index for index, repair in enumerate(repairs) if repair.added_claims > 0]
    scenarios: dict[str, dict[str, float | int | str]] = {}
    for name, assumptions in _assumptions().items():
        projections = [project_p4((cost,), assumptions) for cost in costs]
        active = [projections[index] for index in active_indices]
        scenarios[name] = {
            "evidence_class": "analytical_projection_not_hardware_measurement",
            "clock_mhz": assumptions.clock_mhz,
            "psram_bandwidth_mb_s": assumptions.psram_bandwidth_mb_s,
            "flash_bandwidth_mb_s": assumptions.flash_bandwidth_mb_s,
            "all_unresolved_mean_virtual_latency_ms": sum(
                item.virtual_latency_ms for item in projections
            )
            / len(projections),
            "all_unresolved_p95_virtual_latency_ms": _percentile(
                [item.virtual_latency_ms for item in projections], 0.95
            ),
            "active_mean_virtual_latency_ms": (
                sum(item.virtual_latency_ms for item in active) / len(active) if active else 0.0
            ),
            "active_p95_virtual_latency_ms": _percentile(
                [item.virtual_latency_ms for item in active], 0.95
            ),
        }
    return {
        "schema_version": "aethercore.v11-p4-qualification.v1",
        "scope": f"typed exact value repair over {len(states)} targeted training replicas",
        "replay_bundle_sha256": manifest.bundle_sha256,
        "reachability_report_sha256": __import__("hashlib").sha256(
            reachability_report.read_bytes()
        ).hexdigest(),
        "evaluation_final_held_consumed": False,
        "case_count": len(states),
        "active_case_count": len(active_indices),
        "stored_learned_parameters": 0,
        "active_learned_parameters_mean": 0,
        "active_learned_parameters_p95": 0,
        "macs_mean": 0,
        "macs_p95": 0,
        "integer_operations_mean": sum(item.integer_operations for item in costs) / len(costs),
        "integer_operations_p95": _percentile(
            [float(item.integer_operations) for item in costs], 0.95
        ),
        "source_bytes_mean": sum(item.psram_bytes for item in costs) / len(costs),
        "source_bytes_p95": _percentile(
            [float(item.psram_bytes) for item in costs], 0.95
        ),
        "peak_workspace_ram_bytes": max(item.scratch_ram_bytes for item in costs),
        "model_bytes": 0,
        "host_measurement": {
            "work_host_batch_cases": len(states),
            "warmups": warmups,
            "trials": trials,
            "median_batch_ms": median(host_ms),
            "p95_batch_ms": _percentile(host_ms, 0.95),
            "median_per_case_ms": median(host_ms) / len(states),
            "p95_per_case_ms": _percentile(host_ms, 0.95) / len(states),
        },
        "p4_scenarios": scenarios,
        "assumptions": [
            "one scalar integer operation per cycle; no SIMD or accelerator credit",
            "retained replay source spans are sequentially resident in PSRAM",
            "zero flash bytes and zero random reads for this compute-only repair",
            "scratch RAM uses source-span bytes plus 256 bytes per bounded candidate",
            "projections are not board measurements",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--reachability-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--trials", type=int, default=21)
    args = parser.parse_args()
    result = qualify(
        args.bundle,
        args.reachability_report,
        warmups=args.warmups,
        trials=args.trials,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
