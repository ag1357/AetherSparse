#!/usr/bin/env python3
"""Phase 0B.1: retrieval trace cache.

`build` runs the EvidenceSelector over the benchmark once and persists, per
case: the candidate pool (full CandidateScore payloads), the reranked order,
the selected top-8, and the margin.  Controller variants then replay
counterfactually without re-running retrieval (the expensive stage: 98.8% of
eval wall time).

Cache filename encodes (tier pack sha, retrieval config hash, benchmark sha).
Replay determinism: selector.select() is a pure function of the pool, so a
replay through the same select() code reproduces the original trace exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (  # noqa: E402
    BENCHMARK_PATH,
    answer_cases,
    conversation_order,
    load_benchmark,
)

from aethersparse.selection.models import CandidateScore  # noqa: E402
from aethersparse.selection.selector import EvidenceSelector  # noqa: E402


def config_hash(pack_sha: str, candidate_limit: int, probe_scale: float) -> str:
    payload = json.dumps(
        {
            "pack_sha256": pack_sha,
            "candidate_limit": candidate_limit,
            "probe_scale": probe_scale,
            "benchmark_sha256": _sha256(BENCHMARK_PATH),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(args) -> int:
    benchmark_path = Path(args.benchmark) if args.benchmark else BENCHMARK_PATH
    benchmark = load_benchmark(benchmark_path)
    cases = answer_cases(benchmark)
    if args.all_dispositions:
        cases = list(benchmark.cases)
    if args.limit:
        cases = cases[: args.limit]
    cases = conversation_order(cases)

    pack_sha = _sha256(Path(args.pack))
    selector = EvidenceSelector(
        Path(args.pack),
        candidate_limit=args.candidate_limit,
        probe_scale=args.probe_scale,
    )
    records = []
    started = time.perf_counter()
    for i, case in enumerate(cases):
        pool = list(selector.candidates(case.question))
        trace = selector.select(case.question, initial_candidates=pool)
        scores = [c.final_score for c in trace.reranked_candidates[:2]]
        records.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "partition": str(case.partition),
                "categories": list(case.categories),
                "disposition": str(case.accepted_disposition),
                "pool": [c.model_dump() for c in pool],
                "reranked_chunk_ids": [
                    c.chunk_id for c in trace.reranked_candidates
                ],
                "selected_chunk_ids": [c.chunk_id for c in trace.selected_evidence],
                "margin": scores[0] - scores[1] if len(scores) == 2 else 0.0,
                "top1_score": scores[0] if scores else 0.0,
            }
        )
        if (i + 1) % 200 == 0:
            print(f"cached {i + 1}/{len(cases)}", flush=True)
    payload = {
        "config_hash": config_hash(pack_sha, args.candidate_limit, args.probe_scale),
        "pack": str(args.pack),
        "pack_sha256": pack_sha,
        "candidate_limit": args.candidate_limit,
        "probe_scale": args.probe_scale,
        "all_dispositions": args.all_dispositions,
        "benchmark_sha256": _sha256(benchmark_path),
        "build_seconds": round(time.perf_counter() - started, 2),
        "cases": records,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload))
    print(f"cache={out} cases={len(records)} seconds={payload['build_seconds']}")
    return 0


def load_cache(path: Path) -> dict[str, dict]:
    payload = json.loads(Path(path).read_text())
    return payload


def merge(args) -> int:
    """Merge sharded cache builds (same pack/config, disjoint cases)."""

    payloads = [json.loads(Path(path).read_text()) for path in args.inputs]
    first = payloads[0]
    for payload in payloads[1:]:
        for key in ("config_hash", "pack_sha256", "candidate_limit", "probe_scale", "all_dispositions"):
            if payload[key] != first[key]:
                raise ValueError(f"cache shard config mismatch on {key}")
    seen: set[str] = set()
    cases: list[dict] = []
    for payload in payloads:
        for entry in payload["cases"]:
            if entry["case_id"] in seen:
                raise ValueError(f"duplicate case {entry['case_id']} across shards")
            seen.add(entry["case_id"])
            cases.append(entry)
    merged = dict(first)
    merged["cases"] = cases
    merged["benchmark_sha256"] = _sha256(Path(args.parent_benchmark))
    merged["merged_from_shards"] = len(payloads)
    merged["build_seconds"] = round(sum(p["build_seconds"] for p in payloads), 2)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged))
    print(f"merged={out} cases={len(cases)} shards={len(payloads)}")
    return 0


def pool_from_cache(entry: dict) -> list[CandidateScore]:
    return [CandidateScore.model_validate(item) for item in entry["pool"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--pack", required=True)
    b.add_argument("--output", required=True)
    b.add_argument(
        "--benchmark",
        default=None,
        help="benchmark path (default: the frozen v0.5 set); shard files "
        "from v09_shard_benchmark.py enable parallel cache builds",
    )
    b.add_argument("--limit", type=int)
    b.add_argument("--candidate-limit", type=int, default=96)
    b.add_argument("--probe-scale", type=float, default=1.0)
    b.add_argument("--all-dispositions", action="store_true")
    m = sub.add_parser("merge")
    m.add_argument("--inputs", nargs="+", required=True)
    m.add_argument("--parent-benchmark", required=True)
    m.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "build":
        return build(args)
    if args.command == "merge":
        return merge(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
