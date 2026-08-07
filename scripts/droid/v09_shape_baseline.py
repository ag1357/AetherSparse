#!/usr/bin/env python3
"""Phase 2: answer-shape prediction baseline (Lane E).

1. Baseline = the framer's existing deterministic keyword rules
   (infer_answer_shape) plus the majority-class reference.  No tuning:
   the rules are frozen; we measure per-shape precision/recall on the fit
   partition (development view) and the confirmation partition (held-out).
2. Mode-1 -> mode-2 gap: exact answer accuracy with oracle evidence and
   (a) oracle gold shape (mode 1) vs (b) framer-predicted shape (mode 2).
   If the gap exceeds 10 pp, shape prediction needs its own remediation
   before Phase 3.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import load_benchmark  # noqa: E402

from aethersparse.controller.framing import infer_answer_shape  # noqa: E402

import v08_pipeline_eval as harness  # noqa: E402


def shape_metrics(pairs: list[tuple[str, str]]) -> dict:
    """Per-shape precision/recall for (gold, predicted) pairs."""
    shapes = sorted({g for g, _ in pairs} | {p for _, p in pairs})
    out = {}
    for shape in shapes:
        tp = sum(1 for g, p in pairs if g == shape and p == shape)
        fp = sum(1 for g, p in pairs if g != shape and p == shape)
        fn = sum(1 for g, p in pairs if g == shape and p != shape)
        out[shape] = {
            "support": sum(1 for g, _ in pairs if g == shape),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        }
    correct = sum(1 for g, p in pairs if g == p)
    out["_overall"] = {"accuracy": round(correct / len(pairs), 4), "n": len(pairs)}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--trace-cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-gap", action="store_true")
    args = parser.parse_args()

    benchmark = load_benchmark()
    answer_cases = [
        c for c in benchmark.cases if str(c.accepted_disposition) == "ANSWER"
    ]

    majority = Counter(
        str(getattr(c.required_answer_shape, "value", c.required_answer_shape))
        for c in answer_cases
    ).most_common(1)[0][0]

    by_partition: dict[str, list[tuple[str, str]]] = defaultdict(list)
    majority_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for case in answer_cases:
        gold = str(
            getattr(case.required_answer_shape, "value", case.required_answer_shape)
        )
        predicted = str(infer_answer_shape(case.question))
        partition = str(case.partition)
        by_partition[partition].append((gold, predicted))
        majority_pairs[partition].append((gold, majority))
        confusion[gold][predicted] += 1

    report = {
        "majority_class": majority,
        "agreement_by_partition": {
            part: shape_metrics(pairs) for part, pairs in by_partition.items()
        },
        "majority_baseline_by_partition": {
            part: shape_metrics(pairs)["_overall"]
            for part, pairs in majority_pairs.items()
        },
        "confusion_gold_to_predicted": {
            gold: dict(preds.most_common()) for gold, preds in confusion.items()
        },
    }

    if not args.skip_gap:
        gold_overrides = {
            c.case_id: str(
                getattr(c.required_answer_shape, "value", c.required_answer_shape)
            )
            for c in answer_cases
        }
        oracles = frozenset({"candidate", "ranking", "evidence"})
        _, outcomes_mode1, _ = harness.run_evaluation_with_results(
            pack=Path(args.pack),
            benchmark_path=harness.BENCHMARK_PATH,
            limit=None,
            partitions=None,
            oracles=oracles,
            trace_cache=Path(args.trace_cache),
            _frame_shape_overrides=gold_overrides,
        )
        _, outcomes_mode2, _ = harness.run_evaluation_with_results(
            pack=Path(args.pack),
            benchmark_path=harness.BENCHMARK_PATH,
            limit=None,
            partitions=None,
            oracles=oracles,
            trace_cache=Path(args.trace_cache),
        )
        answer_ids = {c.case_id for c in answer_cases}
        exact1 = sum(
            1
            for o in outcomes_mode1
            if o["case_id"] in answer_ids and o.get("exact_answer")
        )
        exact2 = sum(
            1
            for o in outcomes_mode2
            if o["case_id"] in answer_ids and o.get("exact_answer")
        )
        n = len(answer_ids)
        report["mode_gap"] = {
            "mode1_oracle_shape_exact": round(exact1 / n, 4),
            "mode2_predicted_shape_exact": round(exact2 / n, 4),
            "gap_pp": round(100 * (exact1 - exact2) / n, 2),
            "n": n,
        }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    overall = {
        part: m["_overall"] for part, m in report["agreement_by_partition"].items()
    }
    print("agreement:", json.dumps(overall))
    print("majority:", json.dumps(report["majority_baseline_by_partition"]))
    if "mode_gap" in report:
        print("mode gap:", json.dumps(report["mode_gap"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
