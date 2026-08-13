#!/usr/bin/env python3
"""Freeze the v10 aggregate baseline without inventing unavailable metrics."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def baseline(report_path: Path) -> dict[str, Any]:
    with gzip.open(report_path, "rt", encoding="utf-8") as handle:
        report = json.load(handle)
    training_residuals: dict[str, int] = {}
    for row in report.get("per_case", ()):
        if not isinstance(row, dict) or row.get("partition") not in {
            "development",
            "tuning",
        }:
            continue
        failure_class = str(row.get("failure_class", "UNKNOWN"))
        training_residuals[failure_class] = training_residuals.get(failure_class, 0) + 1
    return {
        "schema_version": "aethercore.v11-integration-baseline.v1",
        "configuration_id": "v10-certified-controller",
        "source_report_sha256": _sha256(report_path),
        "replay_bundle_sha256": report["replay_bundle_sha256"],
        "case_count": report["case_count"],
        "exact_case_accuracy": report["current_deterministic_exact_case_accuracy"],
        "canonical_answer_accuracy": report["current_deterministic_canonical_answer_accuracy"],
        "training_reachability": report["controller_failure_reachable_fraction"],
        "training_entity_binding_residual": training_residuals.get("ENTITY_BINDING_WRONG", 0),
        "training_value_not_enumerated_residual": training_residuals.get(
            "VALUE_NOT_ENUMERATED", 0
        ),
        "unsupported_claim_count": None,
        "calibration": None,
        "active_parameters": 0,
        "trajectory_length_median": report["trajectory_length_median"],
        "trajectory_length_p95": report["trajectory_length_p95"],
        "p4_latency": None,
        "p4_latency_status": "RELATIVE_OPERATION_UNITS_ONLY",
        "limitations": [
            "The v10 per-case payload contains controller failures, not all baseline cases.",
            "Matched calibration and route ablations require new v11 case records.",
            "Held-out replay is oracle-evidence controller isolation, not product evaluation.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = baseline(args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
