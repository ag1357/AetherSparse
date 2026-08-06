#!/usr/bin/env python3
"""Phase 1c: decompose strict-recall erosion by question category across tiers.

Reads v050_selector_eval.py reports (reranker stage, by_category strict) at
several tiers and reports per-category per-decade slopes plus each category's
share of total erosion between adjacent tiers and overall.

Pure analysis: no fitting, no benchmark access beyond the reports themselves.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tiers",
        nargs="+",
        required=True,
        metavar="DOCS=REPORT",
        help="e.g. 10000=v07-10k.json 25000=v07-25k.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    tiers: list[tuple[int, dict[str, float]]] = []
    for spec in args.tiers:
        docs, _, path = spec.partition("=")
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        by_cat = report["stages"]["reranker"]["by_category"]
        tiers.append((int(docs), {c: v["article_recall_strict"] for c, v in by_cat.items()}))
    tiers.sort(key=lambda item: item[0])

    categories = sorted(tiers[0][1])
    overall = {}
    # Overall strict per tier is needed for shares; recompute from categories is
    # not exact (category sizes differ), so read overall from the reports too.
    per_category: dict[str, dict[str, object]] = {}
    for category in categories:
        points = [(docs, values[category]) for docs, values in tiers if category in values]
        slopes = []
        for (d0, v0), (d1, v1) in zip(points, points[1:]):
            decades = math.log10(d1 / d0)
            slopes.append({
                "span": f"{d0}->{d1}",
                "delta_pp": round((v1 - v0) * 100, 2),
                "per_decade_pp": round((v1 - v0) / decades * 100, 2),
            })
        total = points[-1][1] - points[0][1]
        decades = math.log10(points[-1][0] / points[0][0])
        per_category[category] = {
            "values": {str(d): round(v, 4) for d, v in points},
            "spans": slopes,
            "total_delta_pp": round(total * 100, 2),
            "per_decade_pp": round(total / decades * 100, 2),
        }

    # Share of total erosion per category (needs category case counts; use the
    # first tier's report by_category n if present, else equal weights).
    first_report = json.loads(Path(args.tiers[0].partition("=")[2]).read_text())
    counts = {
        c: v.get("n") for c, v in first_report["stages"]["reranker"]["by_category"].items()
    }
    output = {
        "tool": "scripts/droid/v08_erosion_decompose.py",
        "tiers": [d for d, _ in tiers],
        "per_category": per_category,
        "category_case_counts": counts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    multi = ("two_source", "three_to_six_source", "comparison")
    for category in categories:
        row = per_category[category]
        print(
            f"{category:22s} total={row['total_delta_pp']:+7.2f} pp "
            f"({row['per_decade_pp']:+7.2f} pp/dec)"
            + ("  <== multi-source" if category in multi else "")
        )
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
