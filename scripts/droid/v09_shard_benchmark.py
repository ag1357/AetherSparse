#!/usr/bin/env python3
"""Split the frozen benchmark into N deterministic shards (Phase 9 battery).

Each shard is a valid FrozenBenchmark (re-frozen with its own content hash,
so load_benchmark's integrity check passes).  Sharding is round-robin over
conversation chains (union-find over prior_case_ids; a chain never splits,
because the harness replays priors from cases already processed in the same
run); within a shard, cases keep benchmark file order so parents precede
children.  The union of shards is exactly the full benchmark.  Used to run
the serial harness in parallel
processes at 25k/100k/397k and to produce the sharded Amendment A trace
corpus (A5 keys each trace to its shard benchmark hash; the manifest records
the parent benchmark sha256).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import BENCHMARK_PATH, load_benchmark  # noqa: E402

from aethersparse.controller.evaluation import freeze_benchmark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    benchmark = load_benchmark(args.benchmark)
    parent_sha = hashlib.sha256(args.benchmark.read_bytes()).hexdigest()
    by_id = {case.case_id: case for case in benchmark.cases}

    # Conversation chains must stay in one shard: the harness replays
    # prior_case_ids from cases already processed in the same run, so a
    # follow-up whose parent lands in another shard fails with
    # "unknown prior case".  Union-find over prior_case_ids edges.
    parent = {case.case_id: case.case_id for case in benchmark.cases}

    def find(case_id: str) -> str:
        while parent[case_id] != case_id:
            parent[case_id] = parent[parent[case_id]]
            case_id = parent[case_id]
        return case_id

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for case in benchmark.cases:
        for prior_id in case.prior_case_ids:
            if prior_id not in by_id:
                raise ValueError(f"{case.case_id} names unknown prior {prior_id}")
            union(case.case_id, prior_id)

    groups: dict[str, list[str]] = {}
    for case in benchmark.cases:
        groups.setdefault(find(case.case_id), []).append(case.case_id)
    ordered_groups = sorted(groups.values(), key=lambda ids: min(ids))

    shard_ids: list[list[str]] = [[] for _ in range(args.shards)]
    for position, group in enumerate(ordered_groups):
        shard_ids[position % args.shards].extend(group)
    shard_membership = {
        case_id: index for index, ids in enumerate(shard_ids) for case_id in ids
    }
    for case in benchmark.cases:
        for prior_id in case.prior_case_ids:
            if shard_membership[prior_id] != shard_membership[case.case_id]:
                raise AssertionError("chain split across shards")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "parent_benchmark_sha256": parent_sha,
        "shards": args.shards,
        "cases_total": len(by_id),
        "chains": sum(1 for ids in groups.values() if len(ids) > 1),
        "shard_files": [],
    }
    for index in range(args.shards):
        members = set(shard_ids[index])
        # Benchmark file order guarantees parents precede children.
        shard_cases = [case for case in benchmark.cases if case.case_id in members]
        frozen = freeze_benchmark(
            shard_cases,
            author_roles=benchmark.author_roles,
            adjudicator_role=benchmark.adjudicator_role,
            evaluator_role=benchmark.evaluator_role,
            auditor_role=benchmark.auditor_role,
            require_full=False,  # shards are subsets; size floors don't apply
        )
        path = args.output_dir / f"shard-{index}.json"
        path.write_text(frozen.model_dump_json(indent=1), encoding="utf-8")
        manifest["shard_files"].append(
            {
                "file": path.name,
                "cases": len(shard_cases),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    print(json.dumps({s["file"]: s["cases"] for s in manifest["shard_files"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
