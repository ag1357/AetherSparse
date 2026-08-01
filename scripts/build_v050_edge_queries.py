#!/usr/bin/env python3
"""Freeze a stratified winning-workload query set for 10k/50k edge profiling."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path

from aethersparse.controller.evaluation import (
    AblationSystem,
    EvaluationOutcome,
    FrozenBenchmark,
    NaturalQueryCase,
    Partition,
)
from aethersparse.controller.framing import QueryFramer
from aethersparse.v050.profiling import ProfileQuery


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=64)
    parser.add_argument(
        "--system",
        default=AblationSystem.FULL_EXTRACTIVE_CONTROLLER.value,
        choices=[system.value for system in AblationSystem],
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    if not 8 <= args.limit <= 256:
        raise SystemExit("limit must be in [8,256]")
    benchmark = FrozenBenchmark.model_validate_json(args.benchmark.read_text(encoding="utf-8"))
    raw_outcomes = json.loads(args.outcomes.read_text(encoding="utf-8"))
    outcomes = tuple(EvaluationOutcome.model_validate(row) for row in raw_outcomes)
    by_id = {
        row.case_id: row for row in outcomes if row.system.value == args.system
    }
    categories: dict[str, deque[NaturalQueryCase]] = defaultdict(deque)
    for case in benchmark.cases:
        if case.partition is not Partition.FINAL_HELD or case.case_id not in by_id:
            continue
        categories[case.categories[0]].append(case)
    selected: list[NaturalQueryCase] = []
    names = sorted(categories)
    while len(selected) < args.limit and any(categories.values()):
        for category in names:
            if categories[category] and len(selected) < args.limit:
                selected.append(categories[category].popleft())

    framer = QueryFramer()
    queries: list[ProfileQuery] = []
    for case in selected:
        outcome = by_id[case.case_id]
        frame = framer.frame(case.question)
        interface_payload = json.dumps(
            outcome.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        queries.append(
            ProfileQuery(
                query_id=case.case_id,
                text=case.question,
                relation_families=frame.requested_relation_families,
                entity_ids=outcome.linked_entity_ids,
                document_ids=outcome.retrieved_document_ids[:8],
                retrieval_limit=8,
                max_binary_sections=128,
                deterministic_ops=0,
                neural_macs=outcome.macs,
                model_bytes=outcome.model_bytes,
                interface_bytes=len(interface_payload),
            )
        )
    payload = {
        "profile_query_set_id": "AETHERSPARSE_V050_FINAL_HELD_EDGE_QUERIES_R1",
        "benchmark_identity": benchmark.benchmark_identity,
        "benchmark_content_sha256": benchmark.content_sha256,
        "partition": Partition.FINAL_HELD.value,
        "system": args.system,
        "selection": "deterministic_round_robin_by_primary_category",
        "query_count": len(queries),
        "operation_counter_note": (
            "deterministic_ops=0 means CPU instruction/feature-operation counting was not "
            "instrumented; storage, latency, RAM, interface bytes, model bytes and MACs remain "
            "measured. Hardware projection must not interpret zero as free CPU work."
        ),
        "queries": [query.model_dump(mode="json") for query in queries],
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    print(f"output={args.output}")
    print(f"sha256={hashlib.sha256(serialized.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
