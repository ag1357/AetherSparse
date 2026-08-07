#!/usr/bin/env python3
"""Phase 0A.4: parallel benchmark evaluation over conversation shards.

Cases form conversation chains via prior_case_ids.  Carry state
(predicted_top1) is conversation-local, so sharding by weakly-connected
conversation component preserves the serial semantics exactly for
--discourse-gate in {none, compat}.  The margin gate fits an online lookup
across cases in stream order and is therefore NOT supported in parallel
mode (its trajectory would differ); use it serially.

Each shard runs the unmodified v050_selector_eval.py as its own process with
its own read-only immutable pack connection.  Per-case records are merged
and sorted by case_id; aggregate metrics are recomputed from the records
(strict/lenient over ANSWER cases) so completion order never matters.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import load_benchmark  # noqa: E402


def conversation_components(cases) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ids = {c.case_id for c in cases}
    for case in cases:
        for prior in case.prior_case_ids:
            if prior in ids:
                a, b = find(case.case_id), find(prior)
                if a != b:
                    parent[a] = b
    groups: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        groups[find(case.case_id)].append(case.case_id)
    return list(groups.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-case-output")
    parser.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 1))
    parser.add_argument("--candidate-limit", type=int, default=96)
    parser.add_argument("--probe-scale", type=float, default=1.0)
    parser.add_argument("--discourse-boost", type=float, default=0.0)
    parser.add_argument("--discourse-gate", choices=("none", "compat"), default="none")
    parser.add_argument("--all-dispositions", action="store_true")
    parser.add_argument("--pool-provenance", action="store_true")
    parser.add_argument("--shard-dir", type=Path, default=Path("/tmp/v09-eval-shards"))
    args = parser.parse_args()

    benchmark = load_benchmark()
    cases = list(benchmark.cases)
    components = conversation_components(cases)
    components.sort(key=len)  # balance: round-robin largest-first
    shards: list[list[str]] = [[] for _ in range(args.workers)]
    sizes = [0] * args.workers
    for component in sorted(components, key=len, reverse=True):
        target = sizes.index(min(sizes))
        shards[target].extend(component)
        sizes[target] += len(component)
    shards = [sorted(s) for s in shards if s]

    args.shard_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    procs = []
    script = Path(__file__).parent / "v050_selector_eval.py"
    for i, shard in enumerate(shards):
        ids_path = args.shard_dir / f"cases-{i:03d}.json"
        ids_path.write_text(json.dumps(shard))
        cmd = [
            sys.executable,
            str(script),
            "--pack",
            str(args.pack),
            "--candidate-limit",
            str(args.candidate_limit),
            "--probe-scale",
            str(args.probe_scale),
            "--discourse-boost",
            str(args.discourse_boost),
            "--discourse-gate",
            args.discourse_gate,
            "--case-ids-file",
            str(ids_path),
            "--per-case-output",
            str(args.shard_dir / f"percase-{i:03d}.json"),
            "--output",
            str(args.shard_dir / f"report-{i:03d}.json"),
        ]
        if args.all_dispositions:
            cmd.append("--all-dispositions")
        if args.pool_provenance:
            cmd.append("--pool-provenance")
        procs.append(
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    failures = []
    for i, proc in enumerate(procs):
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            failures.append((i, stderr[-2000:]))
    if failures:
        for i, err in failures:
            print(f"shard {i} FAILED:\n{err}", file=sys.stderr)
        return 1
    wall = time.perf_counter() - started

    # Merge per-case records deterministically.
    merged: list[dict] = []
    for i in range(len(shards)):
        merged.extend(
            json.loads((args.shard_dir / f"percase-{i:03d}.json").read_text())
        )
    merged.sort(key=lambda r: r["case_id"])

    answer = [r for r in merged if r.get("disposition") == "ANSWER"]
    strict = sum(1 for r in answer if r.get("strict")) / max(len(answer), 1)
    lenient = sum(1 for r in answer if r.get("lenient")) / max(len(answer), 1)
    io_rchar = sum(r.get("io_rchar", 0) for r in merged)
    report = {
        "pack": str(args.pack),
        "workers": len(shards),
        "wall_seconds": round(wall, 2),
        "cases": len(merged),
        "answer_cases": len(answer),
        "article_recall_strict": strict,
        "article_recall_lenient": lenient,
        "io_rchar_total": io_rchar,
        "config": {
            "candidate_limit": args.candidate_limit,
            "probe_scale": args.probe_scale,
            "discourse_boost": args.discourse_boost,
            "discourse_gate": args.discourse_gate,
            "all_dispositions": args.all_dispositions,
        },
    }
    Path(args.output).write_text(json.dumps(report, indent=2))
    if args.per_case_output:
        Path(args.per_case_output).write_text(json.dumps(merged, indent=2))
    print(
        f"workers={len(shards)} wall={wall:.1f}s cases={len(merged)} "
        f"strict={strict:.4f} lenient={lenient:.4f}"
    )
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
