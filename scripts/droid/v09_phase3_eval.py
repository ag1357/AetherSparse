#!/usr/bin/env python3
"""Phase 3 experiment harness: dual-metric before/after @10k.

Runs the pipeline harness from the trace cache in mode 2 (oracle evidence)
and mode 3 (product: predicted + retrieved) and reports:
  - exact_surface_accuracy (verbatim match)
  - canonical_value_accuracy (deterministic canonicalization match)
  - disposition accuracy over all 2050 cases (non-answer regression guard)
  - per-category and per-shape canonical deltas for revert decisions
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import load_benchmark  # noqa: E402
from v09_controller_taxonomy import canonicalize  # noqa: E402

import v08_pipeline_eval as harness  # noqa: E402


def _dual(outcomes, results, answer_ids) -> dict:
    surface = canonical = 0
    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    per_shape: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for outcome, result in zip(outcomes, results):
        if outcome["case_id"] not in answer_ids:
            continue
        case = outcome
        accepted = outcome.get("accepted_answers") or ()
        realized = result.answer.text if result.answer is not None else None
        s_ok = bool(outcome.get("exact_answer"))
        c_ok = s_ok or (
            realized is not None
            and canonicalize(realized)
            in {canonicalize(a) for a in accepted}
        )
        surface += int(s_ok)
        canonical += int(c_ok)
        for category in case["categories"]:
            per_category[category][0] += int(c_ok)
            per_category[category][1] += 1
    n = max(len(answer_ids), 1)
    return {
        "exact_surface": round(surface / n, 4),
        "canonical_value": round(canonical / n, 4),
        "n": n,
        "per_category_canonical": {
            k: round(v[0] / v[1], 4) for k, v in sorted(per_category.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--trace-cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--tag", required=True, help="experiment label")
    args = parser.parse_args()

    benchmark = load_benchmark()
    cases_by_id = {c.case_id: c for c in benchmark.cases}
    answer_ids = {
        c.case_id for c in benchmark.cases if str(c.accepted_disposition) == "ANSWER"
    }

    report = {"tag": args.tag, "modes": {}}
    for mode, oracles in (
        ("mode2", frozenset({"candidate", "ranking", "evidence"})),
        ("mode3", frozenset()),
    ):
        _, outcomes, results = harness.run_evaluation_with_results(
            pack=Path(args.pack),
            benchmark_path=harness.BENCHMARK_PATH,
            limit=None,
            partitions=None,
            oracles=oracles,
            trace_cache=Path(args.trace_cache),
        )
        for outcome in outcomes:
            case = cases_by_id[outcome["case_id"]]
            outcome["accepted_answers"] = list(case.accepted_answers)
        dual = _dual(outcomes, results, answer_ids)
        disp_correct = sum(
            1
            for o in outcomes
            if str(cases_by_id[o["case_id"]].accepted_disposition)
            == o["disposition"].split(".")[-1].upper()
            or o["disposition"] == str(cases_by_id[o["case_id"]].accepted_disposition)
        )
        dual["disposition_accuracy_all"] = round(disp_correct / len(outcomes), 4)
        report["modes"][mode] = dual

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    for mode, metrics in report["modes"].items():
        print(
            f"{mode}: surface={metrics['exact_surface']:.4f} "
            f"canonical={metrics['canonical_value']:.4f} "
            f"disposition={metrics['disposition_accuracy_all']:.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
