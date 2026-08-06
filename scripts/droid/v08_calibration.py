#!/usr/bin/env python3
"""Phase 5 (Lane C): calibrate the three control signals from per-case eval data.

Signals:
  1. P(top-1 article correct)   — label: top1_gold (ANSWER cases)
  2. P(entity link correct)     — label: required_entity_ids pageids subset of
     linked_pageids (cases with required entities)
  3. P(question answerable)     — label: disposition in ANSWER/CLARIFY

Fit on tuning+development ONLY (binned monotone PAVA lookup — the
P4-deployable form); report reliability curve, ECE, Brier, and
precision/coverage at candidate thresholds on evaluation+final_held.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v050_common import BENCHMARK_PATH, load_benchmark, write_report  # noqa: E402

FIT_PARTITIONS = ("tuning", "development")
HELD_PARTITIONS = ("evaluation", "final_held")
N_BINS = 10


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-case", type=Path, required=True, nargs="+")
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--feature",
        choices=("margin", "top1_score"),
        default="margin",
        help="raw score to calibrate (fit choice on fit partitions only)",
    )
    return parser.parse_args()


def _pava(values: list[float], weights: list[float]) -> list[float]:
    """Pool-adjacent-violators: monotone non-decreasing fit."""

    levels = list(values)
    counts = list(weights)
    i = 0
    while i < len(levels) - 1:
        if levels[i] > levels[i + 1]:
            total = counts[i] + counts[i + 1]
            merged = (levels[i] * counts[i] + levels[i + 1] * counts[i + 1]) / total
            levels[i : i + 2] = [merged]
            counts[i : i + 2] = [total]
            i = max(0, i - 1)
        else:
            i += 1
    return levels


def _fit_lookup(scores: list[float], labels: list[int]) -> list[tuple[float, float]]:
    """Binned monotone lookup: (bin_upper_edge, P(label=1))."""

    order = sorted(zip(scores, labels))
    scores_sorted = [s for s, _ in order]
    labels_sorted = [l for _, l in order]
    n = len(order)
    edges: list[tuple[float, float]] = []
    means: list[float] = []
    counts: list[float] = []
    for b in range(N_BINS):
        lo = b * n // N_BINS
        hi = (b + 1) * n // N_BINS
        chunk = labels_sorted[lo:hi]
        if not chunk:
            continue
        edges.append((scores_sorted[hi - 1], sum(chunk) / len(chunk)))
        means.append(sum(chunk) / len(chunk))
        counts.append(float(len(chunk)))
    fitted = _pava(means, counts)
    return [(edge, p) for (edge, _), p in zip(edges, fitted)]


def _predict(lookup: list[tuple[float, float]], score: float) -> float:
    for edge, p in lookup:
        if score <= edge:
            return p
    return lookup[-1][1] if lookup else 0.0


def _metrics(pairs: list[tuple[float, int]]) -> dict[str, object]:
    """Reliability curve, ECE, Brier, precision/coverage at thresholds."""

    if not pairs:
        return {"n": 0}
    n = len(pairs)
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    curve = []
    ece = 0.0
    for b in range(N_BINS):
        lo, hi = b / N_BINS, (b + 1) / N_BINS
        chunk = [(p, y) for p, y in pairs if lo <= p < hi or (b == N_BINS - 1 and p == 1.0)]
        if not chunk:
            continue
        mp = sum(p for p, _ in chunk) / len(chunk)
        my = sum(y for _, y in chunk) / len(chunk)
        curve.append({"bin": [round(lo, 2), round(hi, 2)], "n": len(chunk),
                      "mean_predicted": round(mp, 4), "mean_observed": round(my, 4)})
        ece += len(chunk) / n * abs(mp - my)
    thresholds = []
    for t in (0.3, 0.5, 0.7, 0.8, 0.9, 0.95):
        kept = [(p, y) for p, y in pairs if p >= t]
        thresholds.append({
            "threshold": t,
            "coverage": round(len(kept) / n, 4),
            "precision": round(sum(y for _, y in kept) / len(kept), 4) if kept else None,
        })
    base_rate = sum(y for _, y in pairs) / n
    return {
        "n": n,
        "base_rate": round(base_rate, 4),
        "brier": round(brier, 4),
        "brier_skill_vs_base": round(1 - brier / (base_rate * (1 - base_rate) + 1e-12), 4),
        "ece": round(ece, 4),
        "reliability_curve": curve,
        "thresholds": thresholds,
    }


def main() -> int:
    args = _parse_args()
    benchmark = load_benchmark(args.benchmark)
    required = {
        case.case_id: set(case.required_entity_ids) for case in benchmark.cases
    }
    records: list[dict[str, object]] = []
    for path in args.per_case:
        records.extend(json.loads(path.read_text(encoding="utf-8")))

    def split(rows: list[dict[str, object]]) -> tuple[list, list]:
        fit = [r for r in rows if str(r["partition"]) in FIT_PARTITIONS]
        held = [r for r in rows if str(r["partition"]) in HELD_PARTITIONS]
        return fit, held

    feature = args.feature
    report: dict[str, object] = {
        "tool": "scripts/droid/v08_calibration.py",
        "feature": feature,
        "fit_partitions": list(FIT_PARTITIONS),
        "held_partitions": list(HELD_PARTITIONS),
        "signals": {},
    }

    # Signal 1: P(top-1 article correct) — ANSWER cases only.
    rows = [r for r in records if r.get("disposition") == "ANSWER"]
    fit, held = split(rows)
    lookup = _fit_lookup([float(r[feature]) for r in fit],
                         [int(bool(r["top1_gold"])) for r in fit])
    pairs = [(_predict(lookup, float(r[feature])), int(bool(r["top1_gold"]))) for r in held]
    report["signals"]["p_top1_correct"] = {
        "lookup": [[round(e, 6), round(p, 4)] for e, p in lookup],
        "fit_n": len(fit),
        "held": _metrics(pairs),
    }

    # Signal 2: P(entity link correct) — cases with required entities.
    rows = [
        r for r in records
        if required.get(str(r["case_id"]))
    ]
    if rows:
        fit, held = split(rows)
        labels_fit = [
            int(required[str(r["case_id"])] <= set(r.get("linked_entity_ids") or []))
            for r in fit
        ]
        lookup = _fit_lookup([float(r[feature]) for r in fit], labels_fit)
        pairs = [
            (
                _predict(lookup, float(r[feature])),
                int(required[str(r["case_id"])] <= set(r.get("linked_entity_ids") or [])),
            )
            for r in held
        ]
        report["signals"]["p_entity_link_correct"] = {
            "lookup": [[round(e, 6), round(p, 4)] for e, p in lookup],
            "fit_n": len(fit),
            "held": _metrics(pairs),
        }

    # Signal 3: P(answerable) — all cases; answerable = ANSWER or CLARIFY.
    rows = [r for r in records if r.get("disposition")]
    fit, held = split(rows)
    lookup = _fit_lookup(
        [float(r[feature]) for r in fit],
        [int(r["disposition"] in ("ANSWER", "CLARIFY")) for r in fit],
    )
    pairs = [
        (_predict(lookup, float(r[feature])),
         int(r["disposition"] in ("ANSWER", "CLARIFY")))
        for r in held
    ]
    report["signals"]["p_answerable"] = {
        "lookup": [[round(e, 6), round(p, 4)] for e, p in lookup],
        "fit_n": len(fit),
        "held": _metrics(pairs),
    }

    write_report(args.output, report)
    for name, signal in report["signals"].items():
        held_m = signal["held"]
        print(
            f"{name}: n={held_m.get('n')} base={held_m.get('base_rate')} "
            f"ece={held_m.get('ece')} brier={held_m.get('brier')}"
        )
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
