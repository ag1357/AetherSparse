#!/usr/bin/env python3
"""Mission 4 gate-refresh: oracle ladder against the current controller.

Re-runs Mission 3's five ladder rungs (candidate / ranking / evidence /
controller oracles) from a Phase 0B trace cache so retrieval is not
re-executed, and reports per-rung strict/lenient/evidence/exact plus
canonical_value, with per-stage marginals.  Leads with mode-3 canonical
(rung 0) per the reporting rule.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import load_benchmark  # noqa: E402
from v09_controller_taxonomy import canonical_match  # noqa: E402

import v08_pipeline_eval as harness  # noqa: E402

RUNGS = (
    ("rung0", frozenset()),
    ("rung1_candidate", frozenset({"candidate"})),
    ("rung2_ranking", frozenset({"candidate", "ranking"})),
    ("rung3_evidence", frozenset({"candidate", "ranking", "evidence"})),
    ("rung4_controller", frozenset({"candidate", "ranking", "evidence", "controller"})),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--trace-cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    benchmark = load_benchmark()
    cases_by_id = {c.case_id: c for c in benchmark.cases}
    answer_ids = {
        c.case_id for c in benchmark.cases if str(c.accepted_disposition) == "ANSWER"
    }

    report = {"pack": args.pack, "trace_cache": args.trace_cache, "rungs": {}}
    for name, oracles in RUNGS:
        metrics_report, outcomes, results = harness.run_evaluation_with_results(
            pack=Path(args.pack),
            benchmark_path=harness.BENCHMARK_PATH,
            limit=None,
            partitions=None,
            oracles=oracles,
            trace_cache=Path(args.trace_cache),
        )
        answer = metrics_report["metrics"]["answer_cases"]
        canonical = 0
        for outcome, result in zip(outcomes, results):
            if outcome["case_id"] not in answer_ids:
                continue
            realized = result.answer.text if result.answer is not None else None
            case = cases_by_id[outcome["case_id"]]
            if outcome.get("exact_answer") or (
                realized is not None
                and any(canonical_match(realized, a) for a in case.accepted_answers)
            ):
                canonical += 1
        n = max(len(answer_ids), 1)
        report["rungs"][name] = {
            "article_recall_strict": answer["article_recall_strict"],
            "article_recall_lenient": answer["article_recall_lenient"],
            "evidence_recall": answer["evidence_recall"],
            "exact_answer_surface": answer["exact_answer_accuracy"],
            "canonical_value": round(canonical / n, 4),
            "stage_attribution": metrics_report["metrics"][
                "stage_attribution_failed_answer_cases"
            ],
        }
        print(
            f"{name}: strict={answer['article_recall_strict']:.4f} "
            f"canonical={canonical / n:.4f}",
            flush=True,
        )

    rungs = report["rungs"]
    report["marginals_pp"] = {
        "candidate_generation": round(
            100
            * (
                rungs["rung1_candidate"]["article_recall_strict"]
                - rungs["rung0"]["article_recall_strict"]
            ),
            2,
        ),
        "ranking": round(
            100
            * (
                rungs["rung2_ranking"]["article_recall_strict"]
                - rungs["rung1_candidate"]["article_recall_strict"]
            ),
            2,
        ),
        "evidence_construction": round(
            100
            * (
                rungs["rung3_evidence"]["evidence_recall"]
                - rungs["rung2_ranking"]["evidence_recall"]
            ),
            2,
        ),
        "controller_residual_canonical": round(
            100 * (1.0 - rungs["rung3_evidence"]["canonical_value"]), 2
        ),
        "controller_gap_canonical_rung0_to_rung3": round(
            100
            * (rungs["rung3_evidence"]["canonical_value"] - rungs["rung0"]["canonical_value"]),
            2,
        ),
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report["marginals_pp"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
