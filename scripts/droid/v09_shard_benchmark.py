#!/usr/bin/env python3
"""Split the frozen benchmark into N deterministic shards (Phase 9 battery).

Each shard is a valid FrozenBenchmark (re-frozen with its own content hash,
so load_benchmark's integrity check passes).  Sharding is round-robin over
sorted case IDs: deterministic, category-mixed, and the union of shards is
exactly the full benchmark.  Used to run the serial harness in parallel
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
    case_ids = sorted(case.case_id for case in benchmark.cases)
    by_id = {case.case_id: case for case in benchmark.cases}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "parent_benchmark_sha256": parent_sha,
        "shards": args.shards,
        "cases_total": len(case_ids),
        "shard_files": [],
    }
    for index in range(args.shards):
        shard_cases = [
            by_id[case_id]
            for position, case_id in enumerate(case_ids)
            if position % args.shards == index
        ]
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
