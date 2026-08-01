#!/usr/bin/env python3
"""Merge complete deterministic qualification shards into one frozen matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from aethersparse.controller.evaluation import (
    AblationSystem,
    EvaluationOutcome,
    FrozenBenchmark,
    evaluate_ablation,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--report", action="append", type=Path, required=True)
    parser.add_argument("--outcomes", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--merged-outcomes", type=Path, required=True)
    return parser.parse_args()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _aggregate_adversarial(reports: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [report["adversarial_verifier"] for report in reports]
    mutation_count = sum(int(row["evaluation_mutation_count"]) for row in rows)
    rejected = sum(
        round(
            float(row["deterministic_mutation_rejection_rate"])
            * int(row["evaluation_mutation_count"])
        )
        for row in rows
    )
    learned_rejected = sum(
        round(float(row["learned_mutation_recall"]) * int(row["evaluation_mutation_count"]))
        for row in rows
    )
    examples = sum(int(row["evaluation_example_count"]) for row in rows)
    learned_correct = sum(
        round(float(row["learned_accuracy"]) * int(row["evaluation_example_count"]))
        for row in rows
    )
    return {
        "experiment_id": "AETHERSPARSE_V050_ADVERSARIAL_VERIFIER_R1_SHARD_AGGREGATE",
        "decision": (
            "SUPPLEMENT_NO_INCREMENTAL_VALUE"
            if all(int(row["incremental_mutations_rejected"]) == 0 for row in rows)
            else "SUPPLEMENT_INCREMENTAL_VETO_MEASURED"
        ),
        "source_answer_count": sum(int(row["source_answer_count"]) for row in rows),
        "train_example_count": sum(int(row["train_example_count"]) for row in rows),
        "evaluation_example_count": examples,
        "evaluation_mutation_count": mutation_count,
        "deterministic_mutation_rejection_rate": (
            rejected / mutation_count if mutation_count else 0.0
        ),
        "learned_accuracy": learned_correct / examples if examples else 0.0,
        "learned_mutation_recall": learned_rejected / mutation_count if mutation_count else 0.0,
        "learned_supported_precision": min(
            float(row["learned_supported_precision"]) for row in rows
        ),
        "learned_false_accept_rate": max(float(row["learned_false_accept_rate"]) for row in rows),
        "incremental_mutations_rejected": sum(
            int(row["incremental_mutations_rejected"]) for row in rows
        ),
        "learned_component_can_only_veto": all(
            bool(row["learned_component_can_only_veto"]) for row in rows
        ),
        "model_bytes": max(int(row["model_bytes"]) for row in rows),
        "retained_in_primary_runtime": any(
            bool(row["retained_in_primary_runtime"]) for row in rows
        ),
        "aggregation_note": (
            "Each deterministic case shard ran the frozen supplement independently; counts and "
            "denominator-weighted rates are merged without retraining a corpus-wide model."
        ),
    }


def main() -> int:
    args = _args()
    if len(args.report) < 2 or len(args.report) != len(args.outcomes):
        raise SystemExit("provide matching report/outcomes paths for at least two shards")
    benchmark = FrozenBenchmark.model_validate_json(args.benchmark.read_text(encoding="utf-8"))
    reports = [_load(path) for path in args.report]
    expected_id = "AETHERSPARSE_V050_SQLITE_CONTROLLER_QUALIFICATION_R2"
    shard_specs: list[tuple[int, int]] = []
    for report in reports:
        if report.get("qualification_id") != expected_id or report.get("qualification_complete"):
            raise SystemExit("input is not an incomplete corrected R2 shard")
        if report["benchmark"]["content_sha256"] != benchmark.content_sha256:
            raise SystemExit("shard benchmark hash mismatch")
        if not report["pack"]["pack_sha256_verified"]:
            raise SystemExit("shard pack checksum was not verified")
        spec = report.get("case_shard")
        if not isinstance(spec, dict):
            raise SystemExit("shard report lacks case_shard identity")
        shard_specs.append((int(spec["index"]), int(spec["count"])))
    counts = {count for _index, count in shard_specs}
    if len(counts) != 1:
        raise SystemExit("shard counts differ")
    count = counts.pop()
    if len(reports) != count or {index for index, _count in shard_specs} != set(range(count)):
        raise SystemExit("shard index set is incomplete")
    pack_hashes = {str(report["pack"]["pack_sha256"]) for report in reports}
    if len(pack_hashes) != 1:
        raise SystemExit("shards used different packs")

    outcomes = tuple(
        EvaluationOutcome.model_validate(row)
        for path in args.outcomes
        for row in _load(path)
    )
    expected_rows = len(benchmark.cases) * len(AblationSystem)
    if len(outcomes) != expected_rows:
        raise SystemExit(f"merged row count {len(outcomes)} != {expected_rows}")
    matrix = evaluate_ablation(benchmark, outcomes, require_complete=True)
    case_order = {case.case_id: index for index, case in enumerate(benchmark.cases)}
    system_order = {system: index for index, system in enumerate(AblationSystem)}
    ordered = tuple(
        sorted(outcomes, key=lambda row: (case_order[row.case_id], system_order[row.system]))
    )
    first = reports[0]
    merged: dict[str, Any] = {
        "qualification_id": expected_id,
        "qualification_complete": True,
        "case_limit": None,
        "case_shard": None,
        "elapsed_seconds": sum(float(report["elapsed_seconds"]) for report in reports),
        "pack": first["pack"],
        "benchmark": first["benchmark"],
        "bounds": first["bounds"],
        "system_semantics": first["system_semantics"],
        "ablation": matrix,
        "adversarial_verifier": _aggregate_adversarial(reports),
        "verified_rag_status": first["verified_rag_status"],
        "measurement_notes": {
            **first["measurement_notes"],
            "shard_merge": (
                f"{count} zero-based strided shards; every case/system pair appears exactly once. "
                "Elapsed seconds is summed worker time; latency rows were measured under "
                "concurrent host load."
            ),
        },
        "merge": {
            "shard_count": count,
            "shards": [
                {
                    "index": index,
                    "report_filename": report_path.name,
                    "outcomes_filename": outcomes_path.name,
                }
                for (index, _count), report_path, outcomes_path in sorted(
                    zip(shard_specs, args.report, args.outcomes, strict=True)
                )
            ],
        },
    }
    outcome_payload = json.dumps(
        [row.model_dump(mode="json") for row in ordered],
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"
    report_payload = json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.merged_outcomes.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report_payload, encoding="utf-8")
    args.merged_outcomes.write_text(outcome_payload, encoding="utf-8")
    print(f"report={args.output}")
    print(f"report_sha256={hashlib.sha256(report_payload.encode()).hexdigest()}")
    print(f"outcomes={args.merged_outcomes}")
    print(f"outcomes_sha256={hashlib.sha256(outcome_payload.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
