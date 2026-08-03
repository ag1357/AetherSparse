#!/usr/bin/env python3
"""Evaluate the EvidenceSelector against the frozen V050 benchmark (read-only).

Computes lenient (any-gold-intersect) and strict (gold-subset) article recall
at the selector's selected-evidence cutoff, overall, per partition, and per
category.  Gold matching is at the pageid component only.

Tuning discipline: fit/decide on tuning+development only.  evaluation and
final_held are reported for information, never used to pick weights.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import (  # noqa: E402
    BENCHMARK_PATH,
    RecallAccumulator,
    answer_cases,
    conversation_order,
    latency_summary,
    load_benchmark,
    pageid,
    write_report,
)

from aethersparse.selection.models import QuantizedLinearModel  # noqa: E402
from aethersparse.selection.selector import EvidenceSelector  # noqa: E402

STAGES = ("lexical", "fusion", "reranker")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, help="int8 reranker JSON; default bootstrap")
    parser.add_argument("--candidate-limit", type=int, default=64)
    parser.add_argument("--selected-limit", type=int, default=8)
    parser.add_argument("--limit", type=int, help="evaluate only the first N answer cases")
    parser.add_argument(
        "--partitions",
        nargs="+",
        default=None,
        help="restrict to these partitions (tuning development evaluation final_held)",
    )
    parser.add_argument(
        "--discourse-boost",
        type=float,
        default=0.0,
        help="additive boost for candidates from the parent turn's predicted top-1 document",
    )
    parser.add_argument(
        "--per-case-output",
        type=Path,
        default=None,
        help="dump per-case reranker-stage margins and pass flags (coverage tables)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    benchmark = load_benchmark(args.benchmark)
    cases = answer_cases(benchmark)
    if args.partitions:
        wanted = set(args.partitions)
        cases = [case for case in cases if case.partition.value in wanted]
    if args.limit is not None:
        cases = cases[: args.limit]
    cases = conversation_order(cases)

    model = (
        QuantizedLinearModel.model_validate_json(args.model.read_text(encoding="utf-8"))
        if args.model
        else None
    )
    selector = EvidenceSelector(
        args.pack,
        model,
        candidate_limit=args.candidate_limit,
        selected_limit=args.selected_limit,
    )

    accumulators = {stage: RecallAccumulator() for stage in STAGES}
    generation_latencies: list[float] = []
    select_latencies: dict[str, list[float]] = {stage: [] for stage in STAGES}
    predicted_top1: dict[str, str] = {}
    per_case: list[dict[str, object]] | None = [] if args.per_case_output else None
    started = time.time()

    for index, case in enumerate(cases, start=1):
        carry_doc = None
        if args.discourse_boost > 0.0 and case.prior_case_ids:
            # The carried document is the parent turn's predicted top-1 at the
            # final (reranker) stage — the system's actual previous answer.
            carry_doc = next(
                (
                    predicted_top1[parent_id]
                    for parent_id in case.prior_case_ids
                    if parent_id in predicted_top1
                ),
                None,
            )
        gen_started = time.perf_counter_ns()
        candidates = selector.candidates(case.question, carry_document_id=carry_doc)
        generation_latencies.append((time.perf_counter_ns() - gen_started) / 1_000_000)
        for stage in STAGES:
            discourse_kwargs: dict[str, object] = {}
            if args.discourse_boost > 0.0:
                discourse_kwargs = {
                    "discourse_document_id": carry_doc,
                    "discourse_boost": args.discourse_boost,
                }
            select_started = time.perf_counter_ns()
            trace = selector.select(
                case.question,
                stage=stage,
                initial_candidates=candidates,
                **discourse_kwargs,  # type: ignore[arg-type]
            )
            select_latencies[stage].append(
                (time.perf_counter_ns() - select_started) / 1_000_000
            )
            retrieved = {pageid(item.document_id) for item in trace.selected_evidence}
            lenient, strict = accumulators[stage].add(case, retrieved)
            if stage == "reranker":
                if trace.reranked_candidates:
                    predicted_top1[case.case_id] = trace.reranked_candidates[0].document_id
                if per_case is not None:
                    scores = [
                        item.final_score for item in trace.reranked_candidates[:2]
                    ]
                    margin = scores[0] - scores[1] if len(scores) == 2 else 0.0
                    per_case.append(
                        {
                            "case_id": case.case_id,
                            "partition": str(case.partition),
                            "categories": list(case.categories),
                            "margin": margin,
                            "top1_score": scores[0] if scores else 0.0,
                            "lenient": lenient,
                            "strict": strict,
                        }
                    )
        if index % 100 == 0 or index == len(cases):
            print(f"evaluated {index}/{len(cases)} cases", file=sys.stderr, flush=True)

    pack_sha256 = hashlib.sha256(args.pack.read_bytes()).hexdigest()
    report = {
        "harness": "scripts/droid/v050_selector_eval.py",
        "benchmark_identity": benchmark.benchmark_identity,
        "benchmark_sha256": benchmark.content_sha256,
        "pack": str(args.pack),
        "pack_sha256": pack_sha256,
        "config": {
            "candidate_limit": args.candidate_limit,
            "selected_limit": args.selected_limit,
            "model": str(args.model) if args.model else "bootstrap-default",
            "model_identity": selector.model.training_identity,
            "discourse_boost": args.discourse_boost,
            "gold_matching": "pageid",
            "answer_cases": len(cases),
        },
        "elapsed_seconds": time.time() - started,
        "candidate_generation_latency": latency_summary(generation_latencies),
        "stages": {
            stage: {
                **accumulators[stage].report(),
                "select_latency_overhead": latency_summary(select_latencies[stage]),
            }
            for stage in STAGES
        },
    }
    write_report(args.output, report)
    if per_case is not None and args.per_case_output is not None:
        args.per_case_output.parent.mkdir(parents=True, exist_ok=True)
        args.per_case_output.write_text(
            json.dumps(per_case, indent=1) + "\n", encoding="utf-8"
        )
    for stage in STAGES:
        overall = report["stages"][stage]["overall"]
        print(
            f"{stage:9s} n={overall['n']:5d} "
            f"lenient={overall['article_recall_lenient']:.4f} "
            f"strict={overall['article_recall_strict']:.4f}"
        )
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
