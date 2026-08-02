#!/usr/bin/env python3
"""Coordinate-search refit of the deterministic fusion weights.

Fits on the benchmark's tuning+development partitions ONLY.  The objective is
strict article recall (gold pageid subset of the top-8), tie-broken by lenient
recall.  Candidate feature vectors are generated once with the current code
and reused for every weight evaluation, so scoring here replicates
``EvidenceSelector._fusion`` exactly (same float summation order, same
``(-score, chunk_id)`` tie-break as ``select(stage="fusion")``).
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (  # noqa: E402
    BENCHMARK_PATH,
    FIT_PARTITIONS,
    answer_cases,
    case_gold_pageids,
    load_benchmark,
    write_report,
)

from aethersparse.selection.models import FEATURE_NAMES  # noqa: E402
from aethersparse.selection.selector import EvidenceSelector  # noqa: E402

GRID = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.16, 0.20, 0.25, 0.30, 0.40, 0.50)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-limit", type=int, default=64)
    parser.add_argument("--selected-limit", type=int, default=8)
    parser.add_argument("--passes", type=int, default=4)
    parser.add_argument(
        "--start-weights",
        type=Path,
        help="JSON list of 14 starting weights; default: current shipped fusion weights",
    )
    parser.add_argument("--cache", type=Path, help="optional candidate cache (pickle)")
    parser.add_argument(
        "--feature-tag",
        required=True,
        help="code-version tag; a cache whose tag differs is regenerated",
    )
    return parser.parse_args()


def _score_recall(
    prepared: list[tuple[list[tuple[tuple[float, ...], str, str]], set[str], int]],
    weights: tuple[float, ...],
) -> tuple[float, float]:
    strict_hits = lenient_hits = 0
    for candidates, gold, selected_limit in prepared:
        scored = [
            (sum(weight * value for weight, value in zip(weights, features)), chunk_id, doc)
            for features, chunk_id, doc in candidates
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        retrieved = {doc for _score, _chunk, doc in scored[:selected_limit]}
        strict_hits += int(bool(gold) and gold <= retrieved)
        lenient_hits += int(bool(gold & retrieved))
    n = len(prepared)
    return strict_hits / n, lenient_hits / n


def main() -> int:
    args = _parse_args()
    benchmark = load_benchmark(args.benchmark)
    cases = [
        case for case in answer_cases(benchmark) if str(case.partition) in FIT_PARTITIONS
    ]

    prepared: list[tuple[list[tuple[tuple[float, ...], str, str]], set[str], int]] = []
    if args.cache and args.cache.is_file():
        with args.cache.open("rb") as stream:
            cached = pickle.load(stream)
        if cached.get("feature_tag") == args.feature_tag and cached.get("pack") == str(
            args.pack
        ) and cached.get("candidate_limit") == args.candidate_limit:
            prepared = cached["prepared"]
            print(f"loaded {len(prepared)} cached candidate sets", file=sys.stderr)
    if not prepared:
        from v050_common import pageid

        selector = EvidenceSelector(args.pack, candidate_limit=args.candidate_limit)
        started = time.time()
        for index, case in enumerate(cases, start=1):
            candidates = [
                (candidate.features, candidate.chunk_id, pageid(candidate.document_id))
                for candidate in selector.candidates(case.question)
            ]
            prepared.append((candidates, case_gold_pageids(case), args.selected_limit))
            if index % 50 == 0 or index == len(cases):
                print(
                    f"generated candidates {index}/{len(cases)} "
                    f"({time.time() - started:.0f}s)",
                    file=sys.stderr,
                    flush=True,
                )
        if args.cache:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            with args.cache.open("wb") as stream:
                pickle.dump(
                    {
                        "feature_tag": args.feature_tag,
                        "pack": str(args.pack),
                        "candidate_limit": args.candidate_limit,
                        "prepared": prepared,
                    },
                    stream,
                )

    if args.start_weights:
        weights = tuple(json.loads(args.start_weights.read_text(encoding="utf-8")))
        if len(weights) != len(FEATURE_NAMES):
            raise ValueError("start-weights must have 14 entries")
    else:
        try:
            from aethersparse.selection.selector import FUSION_WEIGHTS as DEFAULT_WEIGHTS
        except ImportError:
            DEFAULT_WEIGHTS = (
                0.27, 0.12, 0.04, 0.12, 0.08, 0.09, 0.07, 0.05,
                0.03, 0.04, 0.05, 0.02, 0.08, 0.01,
            )
        weights = tuple(DEFAULT_WEIGHTS)
    print(f"start weights: {weights}", file=sys.stderr)
    best_strict, best_lenient = _score_recall(prepared, weights)
    print(f"start strict={best_strict:.4f} lenient={best_lenient:.4f}", file=sys.stderr)

    history = [
        {
            "pass": 0,
            "weights": list(weights),
            "strict": best_strict,
            "lenient": best_lenient,
        }
    ]
    for sweep in range(1, args.passes + 1):
        improved = False
        for coordinate in range(len(FEATURE_NAMES)):
            best_value = weights[coordinate]
            for value in GRID:
                if value == weights[coordinate]:
                    continue
                trial = list(weights)
                trial[coordinate] = value
                strict, lenient = _score_recall(prepared, tuple(trial))
                if (strict, lenient) > (best_strict, best_lenient):
                    best_strict, best_lenient = strict, lenient
                    weights = tuple(trial)
                    best_value = value
                    improved = True
            print(
                f"pass {sweep} {FEATURE_NAMES[coordinate]} -> {best_value} "
                f"strict={best_strict:.4f} lenient={best_lenient:.4f}",
                file=sys.stderr,
                flush=True,
            )
        history.append(
            {
                "pass": sweep,
                "weights": list(weights),
                "strict": best_strict,
                "lenient": best_lenient,
            }
        )
        if not improved:
            break

    report = {
        "tool": "scripts/droid/fit_fusion.py",
        "feature_tag": args.feature_tag,
        "pack": str(args.pack),
        "fit_partitions": list(FIT_PARTITIONS),
        "fit_cases": len(prepared),
        "objective": "strict article recall (pageid subset @ selected_limit), lenient tie-break",
        "feature_names": list(FEATURE_NAMES),
        "fitted_weights": list(weights),
        "fitted_strict": best_strict,
        "fitted_lenient": best_lenient,
        "history": history,
    }
    write_report(args.output, report)
    print(json.dumps({"weights": list(weights), "strict": best_strict, "lenient": best_lenient}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
