#!/usr/bin/env python3
"""Audit the certified v10 report for lawful adaptive-depth supervision."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(report_path: Path) -> dict[str, Any]:
    with gzip.open(report_path, "rt", encoding="utf-8") as handle:
        report = json.load(handle)
    rows = report.get("per_case", ())
    if not isinstance(rows, list):
        raise ValueError("reachability report lacks per_case records")
    eligible = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("partition") in {"development", "tuning"}
    ]
    partitions = Counter(str(row["partition"]) for row in eligible)
    fields_needed = {
        "outcome_if_halted_now",
        "outcome_after_plus_one_cycle",
        "outcome_after_plus_two_cycles",
        "improving_specialist",
        "incremental_compute_cost",
        "workspace_before_cycle",
    }
    present_counts = {
        field: sum(field in row for row in eligible) for field in sorted(fields_needed)
    }
    return {
        "schema_version": "aethercore.depth-data-audit.v1",
        "input_sha256": _sha256(report_path),
        "replay_bundle_sha256": report.get("replay_bundle_sha256"),
        "eligible_failure_rows": len(eligible),
        "partitions": dict(sorted(partitions.items())),
        "trajectory_length_median": report.get("trajectory_length_median"),
        "trajectory_length_p95": report.get("trajectory_length_p95"),
        "estimated_p4_relative_operations_median": report.get(
            "estimated_p4_operations_median"
        ),
        "estimated_p4_relative_operations_p95": report.get("estimated_p4_operations_p95"),
        "counterfactual_field_presence": present_counts,
        "lawful_depth_supervision_available": all(
            count == len(eligible) for count in present_counts.values()
        ),
        "p4_projection_status": "NOT_PROJECTABLE_FROM_RELATIVE_OPERATION_UNITS",
        "required_next_measurement": [
            "capture workspace before each specialist cycle",
            "freeze supported outcome if halted at that state",
            "replay +1/+2 cycles on development and tuning only",
            "record causal specialist and full P4 operation/byte counters",
        ],
        "protected_partition_labels_consumed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
