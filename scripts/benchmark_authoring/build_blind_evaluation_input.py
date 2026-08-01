"""Create the gold-free input consumed by the isolated runtime evaluator."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    BENCHMARK_IDENTITY,
    EVALUATOR_IDENTITY,
    EVALUATOR_PROCESS,
    read_json,
    sha256_text,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = read_json(args.benchmark)
    if benchmark.get("benchmark_identity") != BENCHMARK_IDENTITY:
        raise ValueError("blind evaluator input received the wrong benchmark identity")
    cases: list[dict[str, Any]] = []
    for case in benchmark["cases"]:
        cases.append(
            {
                "case_id": str(case["case_id"]),
                "partition": str(case["partition"]),
                "question": str(case["question"]),
                "prior_case_ids": [str(item) for item in case["prior_case_ids"]],
            }
        )
    payload_hash = sha256_text(
        "\n".join(
            f"{item['case_id']}\t{item['partition']}\t{item['question']}"
            for item in cases
        )
    )
    write_json(
        args.output,
        {
            "benchmark_identity": BENCHMARK_IDENTITY,
            "evaluator_role": {
                "identity": EVALUATOR_IDENTITY,
                "role": "blind_runtime_evaluator",
                "process_identity": EVALUATOR_PROCESS,
                "runtime_access": True,
            },
            "gold_fields_exposed": [],
            "case_count": len(cases),
            "input_sha256": payload_hash,
            "cases": cases,
        },
    )


if __name__ == "__main__":
    main()
