#!/usr/bin/env python3
"""Amendment A6: trace-corpus reporting for V09_QUALIFICATION.md.

Reads trajectory JSONL files (aethersparse.controller.trace) and reports:
  - trace corpus size: cases covered, total operation records, sequence-length
    distribution
  - distribution of legal_actions set size per step
  - per-case block-read distribution: p50 / p95 / max
  - how many cases were solved by more than one distinct operator sequence
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases: list[dict] = []
    for path in args.traces:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                cases.append(json.loads(line))

    seq_lengths = sorted(case["total_steps"] for case in cases)
    case_reads = sorted(case["total_block_reads"] for case in cases)
    legal_sizes: Counter[int] = Counter()
    total_records = 0
    outcomes: Counter[str] = Counter()
    # Distinct operator sequences per case (across all provided trace files).
    sequences: dict[str, set[tuple[int, ...]]] = {}
    solved_sequences: dict[str, set[tuple[int, ...]]] = {}
    for case in cases:
        outcomes[case["outcome"]] += 1
        signature = tuple(record["action_taken"] for record in case["records"])
        sequences.setdefault(case["case_id"], set()).add(signature)
        if case["outcome"] == "correct":
            solved_sequences.setdefault(case["case_id"], set()).add(signature)
        for record in case["records"]:
            total_records += 1
            legal_sizes[len(record["legal_actions"])] += 1

    multi_solved = sum(1 for sigs in solved_sequences.values() if len(sigs) > 1)
    report = {
        "cases_covered": len(cases),
        "distinct_case_ids": len(sequences),
        "total_operation_records": total_records,
        "outcomes": dict(outcomes.most_common()),
        "sequence_length_distribution": {
            "min": seq_lengths[0] if seq_lengths else 0,
            "p50": percentile(seq_lengths, 0.50),
            "p95": percentile(seq_lengths, 0.95),
            "max": seq_lengths[-1] if seq_lengths else 0,
        },
        "legal_actions_size_distribution": {
            str(size): count for size, count in sorted(legal_sizes.items())
        },
        "case_block_reads": {
            "p50": percentile(case_reads, 0.50),
            "p95": percentile(case_reads, 0.95),
            "max": case_reads[-1] if case_reads else 0,
        },
        "cases_solved_by_multiple_sequences": multi_solved,
        "training_eligible_cases": sum(
            1 for case in cases if case["training_eligible"]
        ),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
