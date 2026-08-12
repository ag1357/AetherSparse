#!/usr/bin/env python3
"""One-command S600 export of the real four-tier AetherCore replay bundle.

This command requires existing v09 candidate trace caches and corpus packs.  It
replays only the bounded controller/evidence path with the candidate, ranking,
and evidence oracles enabled; it never invokes corpus candidate retrieval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v08_pipeline_eval as harness

from aethersparse.controller.replay import (
    export_replay_bundle,
    merge_replay_bundles,
    verify_replay_bundle,
)
from aethersparse.controller.trace import TrajectoryTracer


def _tier(value: str) -> tuple[str, Path, Path]:
    parts = value.split("=", 2)
    if len(parts) != 3 or parts[0] not in {"10k", "25k", "100k", "397k"}:
        raise argparse.ArgumentTypeError("tier must be TIER=PACK=TRACE_CACHE")
    pack = Path(parts[1])
    cache = Path(parts[2])
    if not pack.is_file():
        raise argparse.ArgumentTypeError(f"pack not found: {pack}")
    if not cache.is_file():
        raise argparse.ArgumentTypeError(f"trace cache not found: {cache}")
    return parts[0], pack, cache


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        action="append",
        type=_tier,
        required=True,
        help="repeat exactly four times: TIER=PACK=TRACE_CACHE",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=harness.BENCHMARK_PATH,
        help="frozen v050 benchmark",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    tiers = args.tier
    tier_names = [item[0] for item in tiers]
    if sorted(tier_names) != ["100k", "10k", "25k", "397k"]:
        raise ValueError("provide each tier exactly once: 10k, 25k, 100k, 397k")
    work = args.output.with_name(f"{args.output.name}-staging")
    work.mkdir(parents=True, exist_ok=True)
    bundles: list[Path] = []
    for tier, pack, cache in tiers:
        trace_path = work / f"{tier}-controller-trace.jsonl"
        trace_path.write_text("", encoding="utf-8")
        tracer = TrajectoryTracer(path=trace_path)
        harness.run_evaluation(
            pack=pack,
            benchmark_path=args.benchmark,
            limit=None,
            partitions=None,
            oracles=frozenset({"candidate", "ranking", "evidence"}),
            trace_cache=cache,
            _tracer=tracer,
        )
        tier_bundle = work / f"bundle-{tier}"
        manifest = export_replay_bundle((trace_path,), tier_bundle, corpus_tier=tier)
        if manifest.incomplete_case_count:
            raise RuntimeError(
                f"{tier} produced {manifest.incomplete_case_count} incomplete replay cases"
            )
        bundles.append(tier_bundle)
    manifest = merge_replay_bundles(bundles, args.output)
    verify_replay_bundle(args.output)
    print(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
