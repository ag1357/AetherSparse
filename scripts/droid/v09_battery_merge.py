#!/usr/bin/env python3
"""Merge Phase 9 battery shards: mode-3 canonical scaling curve + A6 stats.

Reads the sharded harness outputs (metrics/outcomes/trace JSONL per shard)
and produces, per tier: mode-3 canonical value accuracy (headline), exact
surface, disposition accuracy, strict/lenient article recall, per-category
canonical, and Amendment A6 trace-corpus stats.  Canonical scoring uses the
shipped canonicalize/canonical_match against the full frozen benchmark's
accepted answers; shard union is verified to be exactly the full benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import BENCHMARK_PATH, load_benchmark  # noqa: E402
from v09_controller_taxonomy import canonical_match  # noqa: E402

TIERS = ("25k", "100k", "397k")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--battery-dir", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    benchmark = load_benchmark(args.benchmark)
    accepted = {case.case_id: list(case.accepted_answers) for case in benchmark.cases}
    answer_ids = {
        case.case_id
        for case in benchmark.cases
        if str(case.accepted_disposition) == "ControllerDisposition.ANSWER"
        or case.accepted_disposition == "ANSWER"
    }

    report: dict[str, object] = {"tiers": {}}
    for tier in TIERS:
        outcomes = []
        for path in sorted(args.battery_dir.glob(f"{tier}-s*-outcomes.json")):
            outcomes.extend(json.loads(path.read_text()))
        seen = {row["case_id"] for row in outcomes}
        if seen != set(accepted):
            raise ValueError(
                f"{tier}: shard union {len(seen)} != benchmark {len(accepted)}"
            )

        answer_rows = [row for row in outcomes if row["case_id"] in answer_ids]
        canonical_hits = sum(
            1
            for row in answer_rows
            if row["answer_text"]
            and any(canonical_match(row["answer_text"], a) for a in accepted[row["case_id"]])
        )
        per_category: dict[str, list[bool]] = {}
        for row in answer_rows:
            hit = bool(row["answer_text"]) and any(
                canonical_match(row["answer_text"], a) for a in accepted[row["case_id"]]
            )
            for category in row["categories"]:
                per_category.setdefault(category, []).append(hit)

        # A6 trace-corpus stats
        n_records = 0
        block_reads = 0
        max_step = 0
        outcome_dist: Counter[str] = Counter()
        training_eligible = 0
        wall_us = 0
        for path in sorted(args.battery_dir.glob(f"{tier}-s*-trace.jsonl")):
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                n_records += len(rec["records"])
                block_reads += rec["total_block_reads"]
                max_step = max(max_step, rec["max_step_block_reads"])
                outcome_dist[rec["outcome"]] += 1
                training_eligible += int(bool(rec["training_eligible"]))
                wall_us += rec["wall_us"]

        report["tiers"][tier] = {
            "cases": len(outcomes),
            "answer_cases": len(answer_rows),
            "mode3_canonical": round(canonical_hits / len(answer_rows), 4),
            "mode3_exact_surface": round(
                sum(1 for r in answer_rows if r["exact_answer"]) / len(answer_rows), 4
            ),
            "disposition_accuracy": round(
                sum(1 for r in outcomes if r["disposition_correct"]) / len(outcomes), 4
            ),
            "article_recall_strict": round(
                sum(1 for r in answer_rows if r["article_recall_strict"])
                / len(answer_rows),
                4,
            ),
            "article_recall_lenient": round(
                sum(1 for r in answer_rows if r["article_recall_lenient"])
                / len(answer_rows),
                4,
            ),
            "per_category_canonical": {
                category: round(sum(hits) / len(hits), 4)
                for category, hits in sorted(per_category.items())
            },
            "a6": {
                "trace_records": n_records,
                "records_per_case": round(n_records / len(outcomes), 2),
                "total_block_reads": block_reads,
                "max_step_block_reads": max_step,
                "outcome_distribution": dict(outcome_dist),
                "training_eligible_cases": training_eligible,
                "wall_hours": round(wall_us / 3.6e9, 3),
            },
        }

    args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
    for tier, data in report["tiers"].items():
        print(
            f"{tier}: canonical={data['mode3_canonical']:.4f} "
            f"surface={data['mode3_exact_surface']:.4f} "
            f"strict={data['article_recall_strict']:.4f} "
            f"disposition={data['disposition_accuracy']:.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
